import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal


def _json_safe(value):
    if hasattr(value, "as_dict"):
        return _json_safe(value.as_dict())
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
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
        print(f"Azure - Done {name}")
    except Exception as e:
        errors[name] = str(e)
        results[name] = []
        print(f"Azure - Failed {name}: {e}")


def _take(iterable, limit=100):
    items = []
    for item in iterable:
        items.append(item)
        if len(items) >= limit:
            break
    return items


def _safe_value(default, func):
    try:
        return func()
    except Exception as e:
        return {"error": str(e)} if isinstance(default, dict) else default


def fetch_azure():
    from azure.identity import ClientSecretCredential
    from azure.mgmt.consumption import ConsumptionManagementClient
    from azure.mgmt.costmanagement import CostManagementClient
    from azure.mgmt.costmanagement.models import (
        QueryAggregation,
        QueryDataset,
        QueryDefinition,
        QueryGrouping,
        QueryTimePeriod,
    )
    from azure.mgmt.monitor import MonitorManagementClient
    from azure.mgmt.resourcegraph import ResourceGraphClient
    from azure.mgmt.resourcegraph.models import QueryRequest
    from azure.mgmt.security import SecurityCenter

    try:
        subscription_id = os.getenv("AZURE_SUBSCRIPTION_ID")
        if not subscription_id:
            raise ValueError("AZURE_SUBSCRIPTION_ID is not set")

        creds = ClientSecretCredential(
            tenant_id=os.getenv("AZURE_TENANT_ID"),
            client_id=os.getenv("AZURE_CLIENT_ID"),
            client_secret=os.getenv("AZURE_CLIENT_SECRET"),
        )

        scope = f"/subscriptions/{subscription_id}"
        results = {}
        errors = {}

        resource_graph = ResourceGraphClient(creds)
        security_client = SecurityCenter(creds, subscription_id)
        monitor_client = MonitorManagementClient(creds, subscription_id)
        consumption_client = ConsumptionManagementClient(creds, subscription_id)
        cost_client = CostManagementClient(creds)

        def graph_query(query, limit=500):
            request = QueryRequest(
                subscriptions=[subscription_id],
                query=query,
                options={"result_format": "objectArray", "top": limit},
            )
            response = resource_graph.resources(request)
            return response.data or []

        def fetch_subscription_inventory():
            return {
                "resource_groups": graph_query(
                    """
                    ResourceContainers
                    | where type =~ 'microsoft.resources/subscriptions/resourcegroups'
                    | project id, name, type, location, tags, properties
                    | order by name asc
                    """,
                    limit=500,
                ),
                "resource_summary_by_type_location": graph_query(
                    """
                    Resources
                    | summarize resource_count=count() by type, location
                    | order by resource_count desc
                    """,
                    limit=500,
                ),
                "resources": graph_query(
                    """
                    Resources
                    | project id, name, type, resourceGroup, location, tags, sku,
                              kind, managedBy, identity, properties
                    | order by type asc, name asc
                    """,
                    limit=500,
                ),
            }

        def fetch_compute_inventory():
            return {
                "virtual_machines": graph_query(
                    """
                    Resources
                    | where type =~ 'microsoft.compute/virtualmachines'
                    | project id, name, resourceGroup, location, tags,
                              vmSize=properties.hardwareProfile.vmSize,
                              osType=properties.storageProfile.osDisk.osType,
                              powerState=properties.extended.instanceView.powerState.code,
                              licenseType=properties.licenseType,
                              availabilitySet=properties.availabilitySet.id,
                              zones=zones,
                              networkInterfaces=properties.networkProfile.networkInterfaces,
                              storageProfile=properties.storageProfile,
                              securityProfile=properties.securityProfile,
                              identity
                    | order by name asc
                    """,
                    limit=500,
                ),
                "disks": graph_query(
                    """
                    Resources
                    | where type =~ 'microsoft.compute/disks'
                    | project id, name, resourceGroup, location, tags, sku,
                              diskSizeGB=properties.diskSizeGB,
                              diskState=properties.diskState,
                              osType=properties.osType,
                              encryption=properties.encryption,
                              networkAccessPolicy=properties.networkAccessPolicy
                    | order by name asc
                    """,
                    limit=500,
                ),
                "snapshots": graph_query(
                    """
                    Resources
                    | where type =~ 'microsoft.compute/snapshots'
                    | project id, name, resourceGroup, location, tags, sku,
                              diskSizeGB=properties.diskSizeGB,
                              osType=properties.osType,
                              creationData=properties.creationData
                    | order by name asc
                    """,
                    limit=500,
                ),
                "availability_sets": graph_query(
                    """
                    Resources
                    | where type =~ 'microsoft.compute/availabilitysets'
                    | project id, name, resourceGroup, location, tags, sku, properties
                    | order by name asc
                    """,
                    limit=500,
                ),
            }

        def fetch_network_inventory():
            return {
                "network_interfaces": graph_query(
                    """
                    Resources
                    | where type =~ 'microsoft.network/networkinterfaces'
                    | project id, name, resourceGroup, location, tags,
                              ipConfigurations=properties.ipConfigurations,
                              networkSecurityGroup=properties.networkSecurityGroup.id,
                              virtualMachine=properties.virtualMachine.id
                    | order by name asc
                    """,
                    limit=500,
                ),
                "network_security_groups": graph_query(
                    """
                    Resources
                    | where type =~ 'microsoft.network/networksecuritygroups'
                    | project id, name, resourceGroup, location, tags,
                              securityRules=properties.securityRules,
                              defaultSecurityRules=properties.defaultSecurityRules
                    | order by name asc
                    """,
                    limit=500,
                ),
                "virtual_networks": graph_query(
                    """
                    Resources
                    | where type =~ 'microsoft.network/virtualnetworks'
                    | project id, name, resourceGroup, location, tags,
                              addressSpace=properties.addressSpace,
                              subnets=properties.subnets,
                              peerings=properties.virtualNetworkPeerings
                    | order by name asc
                    """,
                    limit=500,
                ),
                "public_ips": graph_query(
                    """
                    Resources
                    | where type =~ 'microsoft.network/publicipaddresses'
                    | project id, name, resourceGroup, location, tags, sku,
                              ipAddress=properties.ipAddress,
                              publicIPAllocationMethod=properties.publicIPAllocationMethod,
                              dnsSettings=properties.dnsSettings
                    | order by name asc
                    """,
                    limit=500,
                ),
                "load_balancers": graph_query(
                    """
                    Resources
                    | where type =~ 'microsoft.network/loadbalancers'
                    | project id, name, resourceGroup, location, tags, sku, properties
                    | order by name asc
                    """,
                    limit=500,
                ),
                "application_gateways": graph_query(
                    """
                    Resources
                    | where type =~ 'microsoft.network/applicationgateways'
                    | project id, name, resourceGroup, location, tags, sku, properties
                    | order by name asc
                    """,
                    limit=500,
                ),
                "route_tables": graph_query(
                    """
                    Resources
                    | where type =~ 'microsoft.network/routetables'
                    | project id, name, resourceGroup, location, tags, properties
                    | order by name asc
                    """,
                    limit=500,
                ),
                "private_endpoints": graph_query(
                    """
                    Resources
                    | where type =~ 'microsoft.network/privateendpoints'
                    | project id, name, resourceGroup, location, tags, properties
                    | order by name asc
                    """,
                    limit=500,
                ),
            }

        def fetch_data_and_app_inventory():
            return {
                "storage_accounts": graph_query(
                    """
                    Resources
                    | where type =~ 'microsoft.storage/storageaccounts'
                    | project id, name, resourceGroup, location, tags, sku, kind,
                              allowBlobPublicAccess=properties.allowBlobPublicAccess,
                              minimumTlsVersion=properties.minimumTlsVersion,
                              supportsHttpsTrafficOnly=properties.supportsHttpsTrafficOnly,
                              encryption=properties.encryption,
                              networkAcls=properties.networkAcls,
                              privateEndpointConnections=properties.privateEndpointConnections
                    | order by name asc
                    """,
                    limit=500,
                ),
                "key_vaults": graph_query(
                    """
                    Resources
                    | where type =~ 'microsoft.keyvault/vaults'
                    | project id, name, resourceGroup, location, tags,
                              sku=properties.sku,
                              tenantId=properties.tenantId,
                              enableRbacAuthorization=properties.enableRbacAuthorization,
                              enableSoftDelete=properties.enableSoftDelete,
                              enablePurgeProtection=properties.enablePurgeProtection,
                              networkAcls=properties.networkAcls
                    | order by name asc
                    """,
                    limit=500,
                ),
                "sql_resources": graph_query(
                    """
                    Resources
                    | where type startswith 'microsoft.sql/'
                    | project id, name, type, resourceGroup, location, tags, sku, kind, properties
                    | order by type asc, name asc
                    """,
                    limit=500,
                ),
                "postgres_mysql_resources": graph_query(
                    """
                    Resources
                    | where type startswith 'microsoft.dbforpostgresql/'
                       or type startswith 'microsoft.dbformysql/'
                    | project id, name, type, resourceGroup, location, tags, sku, properties
                    | order by type asc, name asc
                    """,
                    limit=500,
                ),
                "cosmos_db_accounts": graph_query(
                    """
                    Resources
                    | where type =~ 'microsoft.documentdb/databaseaccounts'
                    | project id, name, resourceGroup, location, tags, kind,
                              consistencyPolicy=properties.consistencyPolicy,
                              locations=properties.locations,
                              publicNetworkAccess=properties.publicNetworkAccess,
                              networkAclBypass=properties.networkAclBypass,
                              isVirtualNetworkFilterEnabled=properties.isVirtualNetworkFilterEnabled
                    | order by name asc
                    """,
                    limit=500,
                ),
                "app_services": graph_query(
                    """
                    Resources
                    | where type in~ ('microsoft.web/sites', 'microsoft.web/serverfarms')
                    | project id, name, type, resourceGroup, location, tags, kind, sku, properties
                    | order by type asc, name asc
                    """,
                    limit=500,
                ),
                "container_resources": graph_query(
                    """
                    Resources
                    | where type startswith 'microsoft.containerservice/'
                       or type startswith 'microsoft.containerregistry/'
                       or type startswith 'microsoft.app/containerapps'
                    | project id, name, type, resourceGroup, location, tags, sku, properties
                    | order by type asc, name asc
                    """,
                    limit=500,
                ),
            }

        def fetch_identity_authorization():
            return {
                "managed_identities": graph_query(
                    """
                    Resources
                    | where type =~ 'microsoft.managedidentity/userassignedidentities'
                    | project id, name, resourceGroup, location, tags, properties
                    | order by name asc
                    """,
                    limit=500,
                ),
                "role_assignments": graph_query(
                    """
                    AuthorizationResources
                    | where type =~ 'microsoft.authorization/roleassignments'
                    | project id, name, type, resourceGroup,
                              scope=properties.scope,
                              principalId=properties.principalId,
                              principalType=properties.principalType,
                              roleDefinitionId=properties.roleDefinitionId,
                              createdOn=properties.createdOn
                    | order by tostring(scope) asc
                    """,
                    limit=500,
                ),
                "role_definitions": graph_query(
                    """
                    AuthorizationResources
                    | where type =~ 'microsoft.authorization/roledefinitions'
                    | project id, name, type,
                              roleName=properties.roleName,
                              roleType=properties.type,
                              permissions=properties.permissions,
                              assignableScopes=properties.assignableScopes
                    | order by tostring(roleName) asc
                    """,
                    limit=500,
                ),
            }

        def fetch_policy_inventory():
            return {
                "policy_assignments": graph_query(
                    """
                    PolicyResources
                    | where type =~ 'microsoft.authorization/policyassignments'
                    | project id, name, type, resourceGroup, properties
                    | order by name asc
                    """,
                    limit=500,
                ),
                "policy_states": graph_query(
                    """
                    PolicyResources
                    | where type =~ 'microsoft.policyinsights/policystates'
                    | summarize resource_count=count() by complianceState=tostring(properties.complianceState),
                                                      policyAssignmentName=tostring(properties.policyAssignmentName)
                    | order by resource_count desc
                    """,
                    limit=500,
                ),
            }

        def fetch_security():
            security = {}

            security["alerts"] = _safe_value(
                [], lambda: _take(security_client.alerts.list(), limit=100)
            )
            security["active_alerts"] = [
                alert
                for alert in security["alerts"]
                if str(getattr(alert, "state", "")).lower() == "active"
            ][:50]
            security["assessments"] = _safe_value(
                [],
                lambda: _take(security_client.assessments.list(scope), limit=100),
            )
            security["secure_scores"] = _safe_value(
                [], lambda: _take(security_client.secure_scores.list(), limit=50)
            )
            security["regulatory_compliance_standards"] = _safe_value(
                [],
                lambda: _take(
                    security_client.regulatory_compliance_standards.list(), limit=100
                ),
            )
            security["security_pricings"] = _safe_value(
                {}, lambda: security_client.pricings.list(scope).as_dict()
            )

            security["resource_graph_security_summary"] = _safe_value([], lambda: graph_query(
                """
                SecurityResources
                | summarize resource_count=count() by type
                | order by resource_count desc
                """,
                limit=500,
            ))
            security["resource_graph_assessments"] = _safe_value([], lambda: graph_query(
                """
                SecurityResources
                | where type =~ 'microsoft.security/assessments'
                | project id, name, type, resourceGroup,
                          displayName=properties.displayName,
                          statusCode=properties.status.code,
                          statusCause=properties.status.cause,
                          severity=properties.metadata.severity,
                          resourceDetails=properties.resourceDetails
                | order by severity desc
                """,
                limit=500,
            ))

            return security

        def fetch_monitoring():
            now = datetime.now(timezone.utc)
            start = now - timedelta(hours=24)
            one_hour_start = now - timedelta(hours=1)

            activity_filter = (
                f"eventTimestamp ge '{start.isoformat()}' "
                f"and eventTimestamp le '{now.isoformat()}'"
            )
            monitored_resources = graph_query(
                """
                Resources
                | where type in~ ('microsoft.compute/virtualmachines',
                                  'microsoft.storage/storageaccounts',
                                  'microsoft.sql/servers/databases',
                                  'microsoft.web/sites')
                | project id, name, type, resourceGroup, location
                | order by type asc, name asc
                """,
                limit=25,
            )

            metric_samples = []
            timespan = f"{one_hour_start.isoformat()}/{now.isoformat()}"
            for resource in monitored_resources[:10]:
                metricnames = None
                if resource.get("type", "").lower() == "microsoft.compute/virtualmachines":
                    metricnames = (
                        "Percentage CPU,Network In,Network Out,Disk Read Bytes,"
                        "Disk Write Bytes"
                    )

                try:
                    metrics = monitor_client.metrics.list(
                        resource["id"],
                        timespan=timespan,
                        interval="PT1H",
                        metricnames=metricnames,
                        aggregation="Average,Minimum,Maximum,Total",
                        auto_adjust_timegrain=True,
                    )
                    metric_samples.append(
                        {
                            "resource": resource,
                            "metrics": metrics.as_dict(),
                        }
                    )
                except Exception as e:
                    metric_samples.append(
                        {
                            "resource": resource,
                            "error": str(e),
                        }
                    )

            return {
                "activity_logs_sample": _take(
                    monitor_client.activity_logs.list(
                        filter=activity_filter,
                        select=(
                            "eventTimestamp,resourceGroupName,resourceId,"
                            "operationName,status,level,caller,category"
                        ),
                    ),
                    limit=100,
                ),
                "metric_alerts": _take(
                    monitor_client.metric_alerts.list_by_subscription(), limit=100
                ),
                "autoscale_settings": _take(
                    monitor_client.autoscale_settings.list_by_subscription(), limit=100
                ),
                "log_profiles": _take(monitor_client.log_profiles.list(), limit=100),
                "metric_samples": metric_samples,
            }

        def fetch_billing():
            now = datetime.now(timezone.utc)
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            tomorrow = now + timedelta(days=1)

            query = QueryDefinition(
                type="Usage",
                timeframe="Custom",
                time_period=QueryTimePeriod(from_property=month_start, to=tomorrow),
                dataset=QueryDataset(
                    granularity="Monthly",
                    aggregation={
                        "totalCost": QueryAggregation(
                            name="PreTaxCost", function="Sum"
                        )
                    },
                    grouping=[
                        QueryGrouping(type="Dimension", name="ServiceName"),
                        QueryGrouping(type="Dimension", name="ResourceGroupName"),
                    ],
                ),
            )

            return {
                "usage_details_sample": _take(
                    consumption_client.usage_details.list(scope=scope), limit=100
                ),
                "cost_management_month_to_date": cost_client.query.usage(
                    scope=scope, parameters=query
                ),
                "cost_alerts": cost_client.alerts.list(scope=scope),
                "cost_dimensions_sample": _take(
                    cost_client.dimensions.list(scope=scope), limit=100
                ),
                "cost_exports": getattr(
                    cost_client.exports.list(scope=scope), "value", []
                ),
            }

        _safe_section(results, errors, "subscription_inventory", fetch_subscription_inventory)
        _safe_section(results, errors, "compute_inventory", fetch_compute_inventory)
        _safe_section(results, errors, "network_inventory", fetch_network_inventory)
        _safe_section(results, errors, "data_and_app_inventory", fetch_data_and_app_inventory)
        _safe_section(results, errors, "identity_authorization", fetch_identity_authorization)
        _safe_section(results, errors, "policy_inventory", fetch_policy_inventory)
        _safe_section(results, errors, "security", fetch_security)
        _safe_section(results, errors, "monitoring", fetch_monitoring)
        _safe_section(results, errors, "billing", fetch_billing)

        if errors:
            results["partial_errors"] = errors

        return {"azure_full_state": results}

    except Exception as e:
        return {"error": f"Azure fetch failed: {str(e)}"}
