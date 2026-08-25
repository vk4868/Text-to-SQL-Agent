"""Drive the Streamlit app end to end with a fake agent.

Streamlit re-runs the whole script on every interaction, which makes it easy
to build a UI that quietly invokes an expensive pipeline on a widget click.
The first version of this app did exactly that: any rerun with a non-empty
question box fired a full run — twenty-odd seconds and a real BigQuery job.

These tests use Streamlit's own AppTest harness, so they exercise the real
app module rather than a re-implementation of it.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from src.agent import InsightsAgent
from src.exceptions import LLMProviderError
from src.llm.scripted import ScriptedLLMClient
from tests.fakes import data
from tests.fakes.bigquery import FakeBigQueryService
from tests.fakes.factories import WELL_FORMED_ANALYSIS

APP_PATH = Path(__file__).resolve().parents[2] / "app.py"

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

def ask_button(app):
    """Find the Ask button by label.

    Never select it by index: Streamlit orders the form's submit button
    BEFORE the sidebar example buttons, so `button[-1]` is an example. That
    mistake made a test pass while exercising the wrong control entirely.
    """

    for button in app.button:
        if button.label == "Ask":
            return button

    raise AssertionError(
        f"no Ask button; labels were {[b.label for b in app.button]}"
    )


class CountingAgent(InsightsAgent):
    """Records every question it is asked."""

    questions: list[str]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.questions = []

    def run(self, question: str) -> dict:
        self.questions.append(question)
        return super().run(question)


def build_app(
    *,
    responses=None,
    rows=None,
    dry_run_error=None,
):
    """Return (AppTest, agents) with the live agent patched out."""

    agents: list[CountingAgent] = []

    def make_agent(*args, **kwargs):
        agent = CountingAgent(
            bigquery_service=FakeBigQueryService(
                rows=rows,
                dry_run_error=dry_run_error,
            ),
            llm=ScriptedLLMClient(
                responses
                if responses is not None
                else [data.VALID_SQL, WELL_FORMED_ANALYSIS] * 10
            ),
            relationships=list(data.RELATIONSHIPS),
        )
        agents.append(agent)
        return agent

    # st.cache_resource lives for the life of the process, so without this
    # a later test silently reuses an earlier test's agent — and drains its
    # scripted responses.
    import streamlit as st

    st.cache_resource.clear()

    patcher = patch("src.agent.InsightsAgent", side_effect=make_agent)
    patcher.start()

    app = AppTest.from_file(str(APP_PATH), default_timeout=60)

    return app, agents, patcher


@pytest.fixture
def app_factory():
    started = []

    def _build(**kwargs):
        app, agents, patcher = build_app(**kwargs)
        started.append(patcher)
        return app, agents

    yield _build

    for patcher in started:
        patcher.stop()

    import streamlit as st

    st.cache_resource.clear()


def total_questions(agents) -> int:
    return sum(len(agent.questions) for agent in agents)


class TestTheAgentRunsOnlyOnDemand:
    """Each run is a real BigQuery job and two model calls."""

    def test_nothing_runs_on_first_load(self, app_factory):
        app, agents = app_factory()

        app.run()

        assert not app.exception
        assert total_questions(agents) == 0
        assert app.info, "expected a prompt to ask something"

    def test_typing_a_question_does_not_run_it(self, app_factory):
        """The original bug: any rerun fired the pipeline."""

        app, agents = app_factory()
        app.run()

        app.text_input[0].set_value("What were net sales by month?")
        app.run()

        assert total_questions(agents) == 0, (
            "the agent ran without the user asking for it"
        )

    def test_clicking_ask_runs_it_once(self, app_factory):
        app, agents = app_factory()
        app.run()

        app.text_input[0].set_value("What were net sales by month?")
        ask_button(app).click().run()

        assert total_questions(agents) == 1

    def test_rerendering_does_not_run_it_again(self, app_factory):
        """Expanding a section must not cost another pipeline run."""

        app, agents = app_factory()
        app.run()

        app.text_input[0].set_value("What were net sales by month?")
        ask_button(app).click().run()

        app.run()
        app.run()

        assert total_questions(agents) == 1

    def test_an_empty_question_is_refused_without_running(
        self, app_factory
    ):
        app, agents = app_factory()
        app.run()

        app.text_input[0].set_value("   ")
        ask_button(app).click().run()

        assert total_questions(agents) == 0
        assert any("Enter a question" in item.value for item in app.info)


class TestTheSidebarExamples:
    def test_an_example_populates_and_runs(self, app_factory):
        app, agents = app_factory()
        app.run()

        app.sidebar.button[0].click().run()

        assert total_questions(agents) == 1
        assert agents[0].questions[0] == app.text_input[0].value

    def test_every_example_is_offered(self, app_factory):
        import app as app_module

        app, _ = app_factory()
        app.run()

        assert len(app.sidebar.button) == len(
            app_module.EXAMPLE_QUESTIONS
        )


class TestASuccessfulRunRenders:
    @pytest.fixture
    def answered(self, app_factory):
        app, agents = app_factory()
        app.run()
        app.text_input[0].set_value("What were net sales by month?")
        ask_button(app).click().run()
        return app

    def test_no_exception(self, answered):
        assert not answered.exception

    def test_the_outcome_is_shown(self, answered):
        assert [item.value for item in answered.success] == ["Answered"]

    def test_sql_result_and_analysis_all_appear(self, answered):
        headings = [item.value for item in answered.subheader]

        for expected in ("SQL", "Result", "Analysis"):
            assert expected in headings

    def test_the_sql_is_shown_verbatim(self, answered):
        assert any(
            "fact_sales" in block.value for block in answered.code
        )

    def test_the_analysis_text_is_shown(self, answered):
        assert any(
            "DIRECT ANSWER" in item.value for item in answered.text
        )

    def test_the_rows_are_tabulated(self, answered):
        assert len(answered.dataframe) >= 1

    def test_the_cost_metrics_are_shown(self, answered):
        labels = {item.label for item in answered.metric}

        assert {"Rows", "Repairs", "Tokens", "Time"} <= labels


class TestTheGroundingCheckReachesTheUser:
    """The whole point of the project has to be visible in the UI."""

    def test_an_invented_figure_is_surfaced(self, app_factory):
        analysis = WELL_FORMED_ANALYSIS.replace(
            "KEY INSIGHTS:",
            "KEY INSIGHTS:\n- A grand total of $99,123.45.",
        )

        app, _ = app_factory(
            responses=[data.VALID_SQL, analysis]
        )
        app.run()
        app.text_input[0].set_value("What were net sales by month?")
        ask_button(app).click().run()

        errors = [item.value for item in app.error]

        assert any("Not derivable" in message for message in errors)
        assert any("$99,123.45" in message for message in errors)

    def test_a_clean_analysis_says_so(self, app_factory):
        app, _ = app_factory()
        app.run()
        app.text_input[0].set_value("What were net sales by month?")
        ask_button(app).click().run()

        captions = [item.value for item in app.caption]

        assert any("derivable" in caption for caption in captions)
        assert not app.error


class TestFailuresRenderProperly:
    def test_a_guardrail_refusal_is_shown_as_a_refusal(self, app_factory):
        """Not as an error — the safety layer working is a distinct outcome."""

        app, _ = app_factory(
            responses=["SELECT SUM(net_revenue) FROM fact_sales"] * 6
        )
        app.run()
        app.text_input[0].set_value("something unqualified")
        ask_button(app).click().run()

        warnings = [item.value for item in app.warning]

        assert any("Refused by a guardrail" in item for item in warnings)
        assert any("table_access" in item for item in warnings)

    def test_a_model_failure_is_shown_as_an_error(self, app_factory):
        app, _ = app_factory(
            responses=[LLMProviderError("the model is unreachable")]
        )
        app.run()
        app.text_input[0].set_value("anything")
        ask_button(app).click().run()

        errors = [item.value for item in app.error]

        assert any("Failed at stage" in message for message in errors)

    def test_a_zero_row_result_is_explained(self, app_factory):
        app, _ = app_factory(rows=[])
        app.run()
        app.text_input[0].set_value("What were net sales in 1999?")
        ask_button(app).click().run()

        assert not app.exception
        assert any(
            "matched no rows" in item.value for item in app.info
        )


class TestTheAppIsPresentationOnly:
    def test_it_builds_no_graph_or_pipeline_of_its_own(self):
        source = APP_PATH.read_text()

        assert "build_insights_graph" not in source
        assert "SQLExecutionPipeline" not in source
        assert "InsightsAgent" in source
