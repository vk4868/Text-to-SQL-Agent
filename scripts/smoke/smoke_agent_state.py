from src.graph.state import AgentState

def main() -> None:
    state: AgentState = {
        "question": (
            "What were total net sales by month in 2025?"
        ),
        "repair_attempts": 0,
        "repair_history": [],
    }

    print(state)

if __name__ =="__main__":
    main()