import os
import json
import argparse
from dotenv import load_dotenv
import time
from fetchers.azure_fetcher import fetch_azure
from fetchers.aws_fetcher import fetch_aws
from fetchers.gcp_fetcher import fetch_gcp


def main():
    parser = argparse.ArgumentParser(
        description="Cloud Data Fetcher for Systrade Infra"
    )
    parser.add_argument(
        "--source", choices=["azure", "aws", "gcp", "all"], required=True
    )

    args = parser.parse_args()
    data = {}

    load_dotenv()

    if args.source == "azure" or args.source == "all":
        data.update(fetch_azure())
    if args.source == "aws" or args.source == "all":
        data.update(fetch_aws())
    if args.source == "gcp" or args.source == "all":
        data.update(fetch_gcp())

    print("data", data)

    # Output results
    folder_name = "output"
    if not os.path.exists(folder_name):
        os.makedirs(folder_name)

    filename = os.path.join(
        folder_name, f"output_{args.source}_{int(time.time())}.json"
    )
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)

    print(f"\nTask Complete. Data saved to {filename}")


if __name__ == "__main__":
    main()
