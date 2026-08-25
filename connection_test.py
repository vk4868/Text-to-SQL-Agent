import os
from google.cloud import bigquery
from google.api_core.exceptions import GoogleAPIError
from dotenv import load_dotenv
load_dotenv()
PROJECT_ID =os.getenv("GCP_PROJECT_ID")

def main() -> None:
    """Testing authentication and listing the bigquery datasets"""
    try:
        client = bigquery.Client(project=PROJECT_ID)
        print(f"Connected project: {client.project}")
        print("Available datasets:")

        datasets = list(client.list_datasets())
        found = False
        for dataset in datasets:
            found=True
            print(f"{dataset.dataset_id}")
        
        if not found:
            print("No datasets found")

        if not datasets:
            print("no datasets found")
    except GoogleAPIError as e:
        print(f"BigQuery API error: {e}")
    except Exception as e:
        print(f"Failed to connect: {e}")
        
    
if __name__ == "__main__":
    main()


