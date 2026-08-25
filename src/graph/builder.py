from langgraph.graph import END, START, StateGraph

from src.graph.nodes import InsightsGraphNodes
from src.graph.state import AgentState

def build_schema_graph(
    *,
    nodes: InsightsGraphNodes
):
    """Build a minimal graph that retrieves the bigquery schema"""

    builder = StateGraph(AgentState)

    builder.add_node(
        "get_schema",
        nodes.get_schema_node
    )
    builder.add_edge(
        START,
        "get_schema"
    )

    builder.add_edge(
        "get_schema",
        END,
    )

    return builder.compile()

def build_sql_generation_graph(
    *, nodes: InsightsGraphNodes
):
    """Build a graph that retreives schema and generates SQL"""

    builder = StateGraph(AgentState)
    builder.add_node(
        "get_schema",
        nodes.get_schema_node
    )
    builder.add_node(
        "generate_sql",
        nodes.generate_sql_node
    )
    builder.add_edge(START, "get_schema")
    builder.add_edge("get_schema", "generate_sql")
    builder.add_edge("generate_sql", END)

    return builder.compile()


def build_sql_execution_graph(
    *,
    nodes: InsightsGraphNodes,
):
    """Build schema, SQL-generation, and SQL-execution workflow."""

    builder = StateGraph(AgentState)

    builder.add_node(
        "get_schema",
        nodes.get_schema_node,
    )

    builder.add_node(
        "generate_sql",
        nodes.generate_sql_node,
    )

    builder.add_node(
        "execute_sql",
        nodes.execute_sql_node,
    )

    builder.add_edge(
        START,
        "get_schema",
    )

    builder.add_edge(
        "get_schema",
        "generate_sql",
    )

    builder.add_edge(
        "generate_sql",
        "execute_sql",
    )

    builder.add_edge(
        "execute_sql",
        END,
    )

    return builder.compile()

def build_sql_repair_graph(
    *,
    nodes: InsightsGraphNodes
):
    """Build SQL generation, execution and repair workflow"""

    builder = StateGraph(AgentState)

    builder.add_node(
        "get_schema",
        nodes.get_schema_node,
    )
    builder.add_node(
        "generate_sql",
        nodes.generate_sql_node
    )
    builder.add_node(
        "execute_sql",
        nodes.execute_sql_node
    )

    builder.add_node(
        "repair_sql",
        nodes.repair_sql_node
    )

    builder.add_edge(
        START,
        "get_schema"
    )
    builder.add_edge(
        "get_schema",
        "generate_sql"
    )
    builder.add_edge(
        "generate_sql",
        "execute_sql"
    )

    builder.add_conditional_edges(
        "execute_sql",
        nodes.route_after_execution,
        {
            "analyze":END,
            "repair":"repair_sql",
            "end": END
        }
    )

    builder.add_conditional_edges(
        "repair_sql",
        nodes.route_after_repair,
        {
            "execute": "execute_sql",
            "end": END
        }
    )

    return builder.compile()

def build_insights_graph(
    *,
    nodes: InsightsGraphNodes,
):
    """Build the complete question-to-insights graph."""

    builder = StateGraph(AgentState)

    builder.add_node(
        "get_schema",
        nodes.get_schema_node,
    )

    builder.add_node(
        "generate_sql",
        nodes.generate_sql_node,
    )

    builder.add_node(
        "execute_sql",
        nodes.execute_sql_node,
    )

    builder.add_node(
        "repair_sql",
        nodes.repair_sql_node,
    )

    builder.add_node(
        "analyze_result",
        nodes.analyze_result_node,
    )

    builder.add_edge(
        START,
        "get_schema",
    )

    builder.add_conditional_edges(
        "get_schema",
        nodes.route_after_schema,
        {
            "generate":"generate_sql",
            "end": END
        }
    )

    builder.add_conditional_edges(
        "generate_sql",
        nodes.route_after_generation,
        {
            "execute":"execute_sql",
            "end":END
        }
    )

    builder.add_conditional_edges(
        "execute_sql",
        nodes.route_after_execution,
        {
            "analyze": "analyze_result",
            "repair": "repair_sql",
            "end": END,
        },
    )

    builder.add_conditional_edges(
        "repair_sql",
        nodes.route_after_repair,
        {
            "execute": "execute_sql",
            "end": END,
        },
    )

    builder.add_edge(
        "analyze_result",
        END,
    )

    return builder.compile()
