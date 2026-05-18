import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal


def _json_safe(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


def _safe_section(results, errors, name, fetcher):
    try:
        results[name] = _json_safe(fetcher())
        print(f"AWS - Done {name}")
    except Exception as e:
        errors[name] = str(e)
        results[name] = []
        print(f"AWS - Failed {name}: {e}")


def _paginate(client, operation_name, result_key, limit=100, **kwargs):
    items = []

    try:
        paginator = client.get_paginator(operation_name)
    except Exception:
        response = getattr(client, operation_name)(**kwargs)
        return response.get(result_key, [])[:limit]

    for page in paginator.paginate(**kwargs):
        items.extend(page.get(result_key, []))
        if len(items) >= limit:
            break

    return items[:limit]


def _client(session, service_name, region_name=None):
    from botocore.config import Config

    return session.client(
        service_name,
        region_name=region_name,
        config=Config(connect_timeout=10, read_timeout=20, retries={"max_attempts": 2}),
    )


def _safe_call(default, func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception as e:
        return {"error": str(e)} if isinstance(default, dict) else default


def fetch_aws():
    import boto3

    try:
        region = os.getenv("AWS_REGION") or "eu-central-1"
        session_kwargs = {"region_name": region}

        access_key = os.getenv("AWS_ACCESS_KEY_ID")
        secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        session_token = os.getenv("AWS_SESSION_TOKEN")
        if access_key and secret_key:
            session_kwargs["aws_access_key_id"] = access_key
            session_kwargs["aws_secret_access_key"] = secret_key
            if session_token:
                session_kwargs["aws_session_token"] = session_token

        session = boto3.Session(**session_kwargs)
        results = {}
        errors = {}

        sts = _client(session, "sts", region)
        identity = sts.get_caller_identity()
        account_id = identity["Account"]

        def fetch_account_identity():
            iam = _client(session, "iam", "us-east-1")
            return {
                "caller_identity": identity,
                "account_aliases": _safe_call(
                    [], lambda: iam.list_account_aliases().get("AccountAliases", [])
                ),
                "account_summary": _safe_call(
                    {}, lambda: iam.get_account_summary().get("SummaryMap", {})
                ),
                "password_policy": _safe_call(
                    {}, lambda: iam.get_account_password_policy().get("PasswordPolicy", {})
                ),
            }

        def fetch_regions():
            ec2 = _client(session, "ec2", region)
            return ec2.describe_regions(AllRegions=True).get("Regions", [])

        def fetch_ec2_inventory():
            ec2 = _client(session, "ec2", region)
            reservations = _paginate(ec2, "describe_instances", "Reservations", limit=100)
            instances = []
            for reservation in reservations:
                instances.extend(reservation.get("Instances", []))

            return {
                "instances": instances[:100],
                "volumes": _paginate(ec2, "describe_volumes", "Volumes", limit=100),
                "snapshots_owned": _paginate(
                    ec2, "describe_snapshots", "Snapshots", limit=100, OwnerIds=["self"]
                ),
                "vpcs": _paginate(ec2, "describe_vpcs", "Vpcs", limit=100),
                "subnets": _paginate(ec2, "describe_subnets", "Subnets", limit=100),
                "security_groups": _paginate(
                    ec2, "describe_security_groups", "SecurityGroups", limit=100
                ),
                "network_acls": _paginate(
                    ec2, "describe_network_acls", "NetworkAcls", limit=100
                ),
                "route_tables": _paginate(
                    ec2, "describe_route_tables", "RouteTables", limit=100
                ),
                "internet_gateways": _paginate(
                    ec2, "describe_internet_gateways", "InternetGateways", limit=100
                ),
                "nat_gateways": _paginate(
                    ec2, "describe_nat_gateways", "NatGateways", limit=100
                ),
                "addresses": _safe_call(
                    [], lambda: ec2.describe_addresses().get("Addresses", [])
                ),
                "key_pairs": _safe_call(
                    [], lambda: ec2.describe_key_pairs().get("KeyPairs", [])
                ),
            }

        def fetch_load_balancing():
            elbv2 = _client(session, "elbv2", region)
            elb = _client(session, "elb", region)
            return {
                "load_balancers_v2": _safe_call(
                    [],
                    lambda: _paginate(
                        elbv2, "describe_load_balancers", "LoadBalancers", limit=100
                    ),
                ),
                "target_groups": _safe_call(
                    [],
                    lambda: _paginate(
                        elbv2, "describe_target_groups", "TargetGroups", limit=100
                    ),
                ),
                "classic_load_balancers": _safe_call(
                    [],
                    lambda: _paginate(
                        elb,
                        "describe_load_balancers",
                        "LoadBalancerDescriptions",
                        limit=100,
                    ),
                ),
            }

        def fetch_autoscaling():
            autoscaling = _client(session, "autoscaling", region)
            return {
                "auto_scaling_groups": _paginate(
                    autoscaling,
                    "describe_auto_scaling_groups",
                    "AutoScalingGroups",
                    limit=100,
                ),
                "launch_configurations": _paginate(
                    autoscaling,
                    "describe_launch_configurations",
                    "LaunchConfigurations",
                    limit=100,
                ),
                "scaling_policies": _paginate(
                    autoscaling, "describe_policies", "ScalingPolicies", limit=100
                ),
            }

        def fetch_s3_inventory():
            s3 = _client(session, "s3", region)
            buckets = s3.list_buckets().get("Buckets", [])[:100]
            inventory = []

            for bucket in buckets:
                name = bucket["Name"]
                inventory.append(
                    {
                        "bucket": bucket,
                        "location": _safe_call(
                            {},
                            lambda name=name: s3.get_bucket_location(Bucket=name),
                        ),
                        "versioning": _safe_call(
                            {},
                            lambda name=name: s3.get_bucket_versioning(Bucket=name),
                        ),
                        "encryption": _safe_call(
                            {},
                            lambda name=name: s3.get_bucket_encryption(Bucket=name),
                        ),
                        "public_access_block": _safe_call(
                            {},
                            lambda name=name: s3.get_public_access_block(Bucket=name),
                        ),
                        "policy_status": _safe_call(
                            {},
                            lambda name=name: s3.get_bucket_policy_status(Bucket=name),
                        ),
                        "tagging": _safe_call(
                            {},
                            lambda name=name: s3.get_bucket_tagging(Bucket=name),
                        ),
                    }
                )

            return inventory

        def fetch_iam_inventory():
            iam = _client(session, "iam", "us-east-1")
            credential_report = _safe_call(
                {},
                lambda: iam.get_credential_report(),
            )
            if isinstance(credential_report, dict) and "Content" in credential_report:
                credential_report["Content"] = credential_report["Content"].decode(
                    "utf-8", errors="replace"
                )

            return {
                "users": _paginate(iam, "list_users", "Users", limit=100),
                "groups": _paginate(iam, "list_groups", "Groups", limit=100),
                "roles": _paginate(iam, "list_roles", "Roles", limit=100),
                "customer_managed_policies": _paginate(
                    iam, "list_policies", "Policies", limit=100, Scope="Local"
                ),
                "virtual_mfa_devices": _paginate(
                    iam, "list_virtual_mfa_devices", "VirtualMFADevices", limit=100
                ),
                "server_certificates": _paginate(
                    iam,
                    "list_server_certificates",
                    "ServerCertificateMetadataList",
                    limit=100,
                ),
                "credential_report": credential_report,
            }

        def fetch_monitoring():
            cloudwatch = _client(session, "cloudwatch", region)
            logs = _client(session, "logs", region)
            now = datetime.now(timezone.utc)
            start = now - timedelta(hours=1)

            metrics = _safe_call(
                [],
                lambda: _paginate(
                    cloudwatch,
                    "list_metrics",
                    "Metrics",
                    limit=100,
                    RecentlyActive="PT3H",
                ),
            )
            alarms = _safe_call(
                [],
                lambda: _paginate(
                    cloudwatch,
                    "describe_alarms",
                    "MetricAlarms",
                    limit=100,
                ),
            )

            cpu_metric_data = _safe_call(
                {},
                lambda: cloudwatch.get_metric_data(
                    MetricDataQueries=[
                        {
                            "Id": "ec2_cpu_avg",
                            "MetricStat": {
                                "Metric": {
                                    "Namespace": "AWS/EC2",
                                    "MetricName": "CPUUtilization",
                                },
                                "Period": 3600,
                                "Stat": "Average",
                            },
                            "ReturnData": True,
                        }
                    ],
                    StartTime=start,
                    EndTime=now,
                ),
            )

            return {
                "alarms": alarms,
                "metrics_sample": metrics,
                "ec2_cpu_metric_sample": cpu_metric_data,
                "dashboards": _safe_call(
                    [],
                    lambda: _paginate(
                        cloudwatch, "list_dashboards", "DashboardEntries", limit=100
                    ),
                ),
                "log_groups": _safe_call(
                    [],
                    lambda: _paginate(
                        logs, "describe_log_groups", "logGroups", limit=100
                    ),
                ),
                "metric_filters": _safe_call(
                    [],
                    lambda: _paginate(
                        logs, "describe_metric_filters", "metricFilters", limit=100
                    ),
                ),
            }

        def fetch_audit_security():
            cloudtrail = _client(session, "cloudtrail", region)
            config = _client(session, "config", region)
            securityhub = _client(session, "securityhub", region)
            guardduty = _client(session, "guardduty", region)
            access_analyzer = _client(session, "accessanalyzer", region)

            trails = _safe_call(
                [], lambda: cloudtrail.describe_trails(includeShadowTrails=True).get("trailList", [])
            )
            trail_statuses = []
            for trail in trails[:25]:
                name = trail.get("Name")
                if name:
                    trail_statuses.append(
                        {
                            "trail": name,
                            "status": _safe_call(
                                {},
                                lambda name=name: cloudtrail.get_trail_status(Name=name),
                            ),
                        }
                    )

            detectors = _safe_call(
                [], lambda: guardduty.list_detectors().get("DetectorIds", [])
            )
            guardduty_findings = []
            for detector_id in detectors[:5]:
                finding_ids = _safe_call(
                    [],
                    lambda detector_id=detector_id: guardduty.list_findings(
                        DetectorId=detector_id, MaxResults=20
                    ).get("FindingIds", []),
                )
                if finding_ids:
                    guardduty_findings.extend(
                        _safe_call(
                            [],
                            lambda detector_id=detector_id, finding_ids=finding_ids: guardduty.get_findings(
                                DetectorId=detector_id, FindingIds=finding_ids
                            ).get("Findings", []),
                        )
                    )

            return {
                "cloudtrail_trails": trails,
                "cloudtrail_trail_statuses": trail_statuses,
                "cloudtrail_recent_events": _safe_call(
                    [],
                    lambda: cloudtrail.lookup_events(MaxResults=50).get("Events", []),
                ),
                "config_recorders": _safe_call(
                    [],
                    lambda: config.describe_configuration_recorders().get(
                        "ConfigurationRecorders", []
                    ),
                ),
                "config_delivery_channels": _safe_call(
                    [],
                    lambda: config.describe_delivery_channels().get(
                        "DeliveryChannels", []
                    ),
                ),
                "config_rules": _safe_call(
                    [],
                    lambda: _paginate(
                        config, "describe_config_rules", "ConfigRules", limit=100
                    ),
                ),
                "config_compliance_summary": _safe_call(
                    {},
                    lambda: config.get_compliance_summary_by_config_rule(),
                ),
                "securityhub_findings_sample": _safe_call(
                    [],
                    lambda: securityhub.get_findings(MaxResults=50).get("Findings", []),
                ),
                "guardduty_detectors": detectors,
                "guardduty_findings_sample": guardduty_findings[:50],
                "access_analyzers": _safe_call(
                    [],
                    lambda: access_analyzer.list_analyzers().get("analyzers", []),
                ),
            }

        def fetch_raw_logs():
            cloudtrail = _client(session, "cloudtrail", region)
            logs = _client(session, "logs", region)
            now = datetime.now(timezone.utc)
            start = now - timedelta(hours=24)
            start_ms = int(start.timestamp() * 1000)
            end_ms = int(now.timestamp() * 1000)

            log_groups = _safe_call(
                [],
                lambda: _paginate(logs, "describe_log_groups", "logGroups", limit=25),
            )
            cloudwatch_log_events = []
            for log_group in log_groups[:10]:
                log_group_name = log_group.get("logGroupName")
                if not log_group_name:
                    continue

                events = _safe_call(
                    [],
                    lambda log_group_name=log_group_name: logs.filter_log_events(
                        logGroupName=log_group_name,
                        startTime=start_ms,
                        endTime=end_ms,
                        limit=20,
                    ).get("events", []),
                )
                cloudwatch_log_events.append(
                    {
                        "log_group_name": log_group_name,
                        "events": events,
                    }
                )

            return {
                "collection_window": {
                    "start": start.isoformat(),
                    "end": now.isoformat(),
                },
                "cloudtrail_lookup_events": _safe_call(
                    [],
                    lambda: cloudtrail.lookup_events(
                        StartTime=start,
                        EndTime=now,
                        MaxResults=50,
                    ).get("Events", []),
                ),
                "cloudwatch_log_groups_sample": log_groups,
                "cloudwatch_log_events_sample": cloudwatch_log_events,
                "notes": [
                    "CloudTrail LookupEvents returns recent management/Insights events, not historical S3-delivered trail files.",
                    "CloudWatch Logs samples are limited to the first 10 visible log groups and 20 events per group for the last 24 hours.",
                ],
            }

        def fetch_database_and_compute_services():
            rds = _client(session, "rds", region)
            lambda_client = _client(session, "lambda", region)
            ecs = _client(session, "ecs", region)
            eks = _client(session, "eks", region)
            ecr = _client(session, "ecr", region)
            kms = _client(session, "kms", region)

            return {
                "rds_instances": _safe_call(
                    [],
                    lambda: _paginate(
                        rds, "describe_db_instances", "DBInstances", limit=100
                    ),
                ),
                "rds_clusters": _safe_call(
                    [],
                    lambda: _paginate(
                        rds, "describe_db_clusters", "DBClusters", limit=100
                    ),
                ),
                "lambda_functions": _safe_call(
                    [],
                    lambda: _paginate(
                        lambda_client, "list_functions", "Functions", limit=100
                    ),
                ),
                "ecs_clusters": _safe_call(
                    [], lambda: ecs.list_clusters(maxResults=100).get("clusterArns", [])
                ),
                "eks_clusters": _safe_call(
                    [], lambda: eks.list_clusters(maxResults=100).get("clusters", [])
                ),
                "ecr_repositories": _safe_call(
                    [],
                    lambda: _paginate(
                        ecr, "describe_repositories", "repositories", limit=100
                    ),
                ),
                "kms_keys": _safe_call(
                    [], lambda: _paginate(kms, "list_keys", "Keys", limit=100)
                ),
            }

        def fetch_billing():
            ce = _client(session, "ce", "us-east-1")
            budgets = _client(session, "budgets", "us-east-1")
            now = datetime.now(timezone.utc).date()
            start = now.replace(day=1).isoformat()
            end = (now + timedelta(days=1)).isoformat()

            return {
                "month_to_date_cost_by_service": _safe_call(
                    {},
                    lambda: ce.get_cost_and_usage(
                        TimePeriod={"Start": start, "End": end},
                        Granularity="MONTHLY",
                        Metrics=["UnblendedCost", "UsageQuantity"],
                        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
                    ),
                ),
                "cost_categories": _safe_call(
                    [],
                    lambda: ce.list_cost_category_definitions().get(
                        "CostCategoryReferences", []
                    ),
                ),
                "budgets": _safe_call(
                    [],
                    lambda: budgets.describe_budgets(AccountId=account_id).get(
                        "Budgets", []
                    ),
                ),
            }

        _safe_section(results, errors, "account_identity", fetch_account_identity)
        _safe_section(results, errors, "regions", fetch_regions)
        _safe_section(results, errors, "ec2_inventory", fetch_ec2_inventory)
        _safe_section(results, errors, "load_balancing", fetch_load_balancing)
        _safe_section(results, errors, "autoscaling", fetch_autoscaling)
        _safe_section(results, errors, "s3_inventory", fetch_s3_inventory)
        _safe_section(results, errors, "iam_inventory", fetch_iam_inventory)
        _safe_section(results, errors, "monitoring", fetch_monitoring)
        _safe_section(results, errors, "audit_security", fetch_audit_security)
        _safe_section(results, errors, "raw_logs", fetch_raw_logs)
        _safe_section(
            results,
            errors,
            "database_and_compute_services",
            fetch_database_and_compute_services,
        )
        _safe_section(results, errors, "billing", fetch_billing)

        if errors:
            results["partial_errors"] = errors

        return {"aws_full_state": results}
    except Exception as e:
        return {"error": f"AWS fetch failed: {str(e)}"}
