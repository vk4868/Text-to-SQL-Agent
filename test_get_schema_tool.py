from src.tools import get_default_tools

get_schema, _ = get_default_tools()

def main() -> None:
    print(f"Tool name = {get_schema.name}")

    print(f"\n Tool description = {get_schema.description}")

    print(f"\n Tool input schema: {get_schema.args}")

    print("\n Tool result:")

    result = get_schema.invoke({})
    print(result)

if __name__ =="__main__":
    main()
    