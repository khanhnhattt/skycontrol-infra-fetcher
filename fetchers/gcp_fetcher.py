import os
from datetime import datetime, timedelta, timezone


def _proto_to_dict(message):
    from google.protobuf.json_format import MessageToDict

    proto = message._pb if hasattr(message, "_pb") else message
    return MessageToDict(proto, preserving_proto_field_name=True)


def _safe_section(results, errors, name, fetcher):
    try:
        results[name] = fetcher()
        print(f"GCP - Done {name}")
    except Exception as e:
        errors[name] = str(e)
        results[name] = []
        print(f"GCP - Failed {name}: {e}")


def _paged_get(session, url, item_key, params=None, page_size=100, limit=100):
    params = dict(params or {})
    params.setdefault("pageSize", page_size)
    items = []
    page_token = None

    while len(items) < limit:
        if page_token:
            params["pageToken"] = page_token

        response = session.get(url, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()
        items.extend(payload.get(item_key, []))

        page_token = payload.get("nextPageToken")
        if not page_token:
            break

    return items[:limit]


def _flatten_aggregated_compute_items(payload, item_key):
    resources = []
    for scoped_items in payload.get("items", {}).values():
        resources.extend(scoped_items.get(item_key, []))
    return resources


def fetch_gcp():
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    from google.cloud import billing_v1, monitoring_v3, resourcemanager_v3
    from google.cloud.monitoring_v3 import TimeInterval
    from google.protobuf.timestamp_pb2 import Timestamp

    try:
        project_id = os.getenv("GCP_PROJECT_ID")
        if not project_id:
            raise ValueError("GCP_PROJECT_ID is not set")

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        session = AuthorizedSession(credentials)

        crm_client = resourcemanager_v3.ProjectsClient(credentials=credentials)
        billing_client = billing_v1.CloudBillingClient(credentials=credentials)
        metric_client = monitoring_v3.MetricServiceClient(credentials=credentials)
        alert_client = monitoring_v3.AlertPolicyServiceClient(credentials=credentials)
        uptime_client = monitoring_v3.UptimeCheckServiceClient(credentials=credentials)

        project_name = f"projects/{project_id}"
        project_path = f"projects/{project_id}"
        results = {}
        errors = {}

        def fetch_project_metadata():
            project = crm_client.get_project(name=project_name, timeout=20)
            return _proto_to_dict(project)

        def fetch_iam_policy():
            policy = crm_client.get_iam_policy(resource=project_name, timeout=20)
            return _proto_to_dict(policy)

        def fetch_enabled_services():
            services = _paged_get(
                session,
                f"https://serviceusage.googleapis.com/v1/{project_path}/services",
                "services",
                params={"filter": "state:ENABLED"},
                page_size=200,
                limit=200,
            )
            return [
                {
                    "name": service.get("name"),
                    "title": service.get("config", {}).get("title"),
                    "state": service.get("state"),
                }
                for service in services
            ]

        def fetch_asset_inventory():
            response = session.get(
                f"https://cloudasset.googleapis.com/v1/{project_path}:searchAllResources",
                params={"pageSize": 100},
                timeout=20,
            )
            response.raise_for_status()
            return response.json().get("results", [])

        def fetch_compute_inventory():
            base = f"https://compute.googleapis.com/compute/v1/projects/{project_id}"
            inventory = {}

            aggregated_resources = {
                "instances": "instances",
                "disks": "disks",
                "subnetworks": "subnetworks",
                "forwarding_rules": "forwardingRules",
                "routers": "routers",
            }
            for result_key, api_key in aggregated_resources.items():
                response = session.get(f"{base}/aggregated/{api_key}", timeout=20)
                response.raise_for_status()
                inventory[result_key] = _flatten_aggregated_compute_items(
                    response.json(), api_key
                )[:100]

            for result_key, api_key in {
                "networks": "networks",
                "firewalls": "firewalls",
                "routes": "routes",
                "snapshots": "snapshots",
            }.items():
                inventory[result_key] = _paged_get(
                    session,
                    f"{base}/global/{api_key}",
                    "items",
                    page_size=100,
                    limit=100,
                )

            return inventory

        def fetch_service_accounts():
            return _paged_get(
                session,
                "https://iam.googleapis.com/v1/projects/"
                f"{project_id}/serviceAccounts",
                "accounts",
                page_size=100,
                limit=100,
            )

        def fetch_bigquery_inventory():
            datasets = _paged_get(
                session,
                f"https://bigquery.googleapis.com/bigquery/v2/projects/{project_id}/datasets",
                "datasets",
                page_size=100,
                limit=100,
            )

            inventory = []
            for dataset in datasets:
                dataset_id = dataset.get("datasetReference", {}).get("datasetId")
                tables = []
                if dataset_id:
                    tables = _paged_get(
                        session,
                        "https://bigquery.googleapis.com/bigquery/v2/projects/"
                        f"{project_id}/datasets/{dataset_id}/tables",
                        "tables",
                        page_size=100,
                        limit=100,
                    )
                inventory.append({"dataset": dataset, "tables": tables})

            return inventory

        def fetch_storage_inventory():
            return _paged_get(
                session,
                "https://storage.googleapis.com/storage/v1/b",
                "items",
                params={"project": project_id},
                page_size=100,
                limit=100,
            )

        def fetch_cloud_sql_inventory():
            response = session.get(
                f"https://sqladmin.googleapis.com/sql/v1beta4/projects/{project_id}/instances",
                timeout=20,
            )
            response.raise_for_status()
            return response.json().get("items", [])

        def fetch_billing():
            billing = {}
            try:
                billing_info = billing_client.get_project_billing_info(
                    name=project_name, timeout=20
                )
                billing["project_billing_info"] = _proto_to_dict(billing_info)
            except Exception as e:
                billing["project_billing_info_error"] = str(e)

            try:
                billing_accounts = billing_client.list_billing_accounts(timeout=20)
                billing["visible_billing_accounts"] = [
                    _proto_to_dict(account) for account in billing_accounts
                ]
            except Exception as e:
                billing["visible_billing_accounts_error"] = str(e)

            return billing

        def fetch_monitoring():
            now = datetime.now(timezone.utc)
            start = now - timedelta(hours=1)
            start_pb = Timestamp()
            start_pb.FromDatetime(start)
            end_pb = Timestamp()
            end_pb.FromDatetime(now)
            interval = TimeInterval(start_time=start_pb, end_time=end_pb)

            metric_filters = {
                "compute_cpu_utilization": (
                    'metric.type="compute.googleapis.com/instance/cpu/utilization"'
                ),
                "compute_received_bytes": (
                    'metric.type="compute.googleapis.com/instance/network/received_bytes_count"'
                ),
                "compute_sent_bytes": (
                    'metric.type="compute.googleapis.com/instance/network/sent_bytes_count"'
                ),
                "compute_disk_read_bytes": (
                    'metric.type="compute.googleapis.com/instance/disk/read_bytes_count"'
                ),
            }

            samples = {}
            for metric_name, metric_filter in metric_filters.items():
                series = metric_client.list_time_series(
                    name=project_name,
                    filter=metric_filter,
                    interval=interval,
                    view=monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
                    timeout=20,
                )
                samples[metric_name] = [
                    _proto_to_dict(item) for _, item in zip(range(10), series)
                ]

            descriptors = metric_client.list_metric_descriptors(
                name=project_name, timeout=20
            )
            alert_policies = alert_client.list_alert_policies(
                name=project_name, timeout=20
            )
            uptime_checks = uptime_client.list_uptime_check_configs(
                parent=project_name, timeout=20
            )

            return {
                "performance_sample": samples,
                "metric_descriptors_sample": [
                    _proto_to_dict(item) for _, item in zip(range(25), descriptors)
                ],
                "alert_policies": [
                    _proto_to_dict(item) for _, item in zip(range(50), alert_policies)
                ],
                "uptime_checks": [
                    _proto_to_dict(item) for _, item in zip(range(50), uptime_checks)
                ],
            }

        def fetch_logging():
            entries_response = session.post(
                "https://logging.googleapis.com/v2/entries:list",
                json={
                    "resourceNames": [project_name],
                    "pageSize": 20,
                    "orderBy": "timestamp desc",
                },
                timeout=20,
            )
            entries_response.raise_for_status()

            sinks = _paged_get(
                session,
                f"https://logging.googleapis.com/v2/{project_path}/sinks",
                "sinks",
                page_size=100,
                limit=100,
            )
            log_metrics = _paged_get(
                session,
                f"https://logging.googleapis.com/v2/{project_path}/metrics",
                "metrics",
                page_size=100,
                limit=100,
            )

            return {
                "recent_entries": entries_response.json().get("entries", []),
                "sinks": sinks,
                "log_based_metrics": log_metrics,
            }

        _safe_section(results, errors, "project_metadata", fetch_project_metadata)
        _safe_section(results, errors, "iam_policy", fetch_iam_policy)
        _safe_section(results, errors, "enabled_services", fetch_enabled_services)
        _safe_section(results, errors, "asset_inventory", fetch_asset_inventory)
        _safe_section(results, errors, "compute_inventory", fetch_compute_inventory)
        _safe_section(results, errors, "service_accounts", fetch_service_accounts)
        _safe_section(results, errors, "bigquery_inventory", fetch_bigquery_inventory)
        _safe_section(results, errors, "storage_inventory", fetch_storage_inventory)
        _safe_section(results, errors, "cloud_sql_inventory", fetch_cloud_sql_inventory)
        _safe_section(results, errors, "monitoring", fetch_monitoring)
        _safe_section(results, errors, "logging", fetch_logging)
        _safe_section(results, errors, "billing", fetch_billing)

        if errors:
            results["partial_errors"] = errors

        return {
            "gcp_full_state": results
        }
    except Exception as e:
        return {"error": f"GCP fetch failed: {str(e)}"}
