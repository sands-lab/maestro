# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from google.cloud import bigquery

from brand_search_optimization.shared_libraries import constants

PROJECT = constants.PROJECT
TABLE_ID = constants.TABLE_ID
LOCATION = constants.LOCATION
DATASET_ID = constants.DATASET_ID
TABLE_ID = constants.TABLE_ID

client = bigquery.Client(project=PROJECT)

# Sample data to insert
data_to_insert = [
    {
        "Title": "Kids' Joggers",
        "Description": "Comfortable and supportive running shoes for active kids. Breathable mesh upper keeps feet cool, while the durable outsole provides excellent traction.",
        "Attributes": "Size: 10 Toddler, Color: Blue/Green",
        "Brand": "BSOAgentTestBrand",
    },
    {
        "Title": "Light-Up Sneakers",
        "Description": "Fun and stylish sneakers with light-up features that kids will love. Supportive and comfortable for all-day play.",
        "Attributes": "Size: 13 Toddler, Color: Silver",
        "Brand": "BSOAgentTestBrand",
    },
    {
        "Title": "School Shoes",
        "Description": "Versatile and comfortable shoes perfect for everyday wear at school. Durable construction with a supportive design.",
        "Attributes": "Size: 12 Preschool, Color: Black",
        "Brand": "BSOAgentTestBrand",
    },
]


def create_dataset_if_not_exists():
    """Creates a BigQuery dataset if it does not already exist."""
    # Construct a BigQuery client object.
    dataset_id = f"{client.project}.{DATASET_ID}"
    dataset = bigquery.Dataset(dataset_id)
    # Use configured default location
    dataset.location = LOCATION
    client.delete_dataset(
        dataset_id, delete_contents=True, not_found_ok=True
    )  # Make an API request.
    dataset = client.create_dataset(dataset)  # Make an API request.
    print("Created dataset {}.{}".format(client.project, dataset.dataset_id))
    return dataset


def populate_bigquery_table():
    """Populates a BigQuery table with the provided data."""
    dataset_ref = create_dataset_if_not_exists()
    if not dataset_ref:
        return

    # Define the schema based on your CREATE TABLE statement
    schema = [
        bigquery.SchemaField("Title", "STRING"),
        bigquery.SchemaField("Description", "STRING"),
        bigquery.SchemaField("Attributes", "STRING"),
        bigquery.SchemaField("Brand", "STRING"),
    ]
    table_id = f"{PROJECT}.{DATASET_ID}.{TABLE_ID}"
    table = bigquery.Table(table_id, schema=schema)
    client.delete_table(table_id, not_found_ok=True)  # Make an API request.
    print("Deleted table '{}'.".format(table_id))
    table = client.create_table(table)  # Make an API request.
    print(
        "Created table {}.{}.{}".format(
            PROJECT, table.dataset_id, table.table_id
        )
    )

    # Use a load job instead of streaming inserts (free tier compatible)
    job_config = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
    )

    load_job = client.load_table_from_json(
        data_to_insert,
        table,
        job_config=job_config,
        location=LOCATION,
    )

    result = load_job.result()  # Waits for the job to complete.
    if load_job.errors:
        print("Errors occurred during load job:")
        for err in load_job.errors:
            print(err)
    elif load_job.error_result:
        print("Load job failed with error:")
        print(load_job.error_result)
    else:
        print(
            f"Successfully loaded {len(data_to_insert)} rows into {PROJECT}.{DATASET_ID}.{TABLE_ID}"
        )


if __name__ == "__main__":
    populate_bigquery_table()
    print(
        "\n--- Instructions on how to add permissions to BQ Table are in the customization.md file ---"
    )
