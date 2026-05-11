import os
from datetime import datetime, timedelta

def fetch_azure():
    from azure.identity import ClientSecretCredential
    from azure.mgmt.resourcegraph import ResourceGraphClient
    from azure.mgmt.resourcegraph.models import QueryRequest
    from azure.mgmt.security import SecurityCenter
    from azure.mgmt.consumption import ConsumptionManagementClient
    from azure.mgmt.monitor import MonitorManagementClient

    try:
        # Clean subscription ID
        raw_sub_id = os.getenv("AZURE_SUBSCRIPTION_ID")

        creds = ClientSecretCredential(
            tenant_id=os.getenv("AZURE_TENANT_ID"),
            client_id=os.getenv("AZURE_CLIENT_ID"),
            client_secret=os.getenv("AZURE_CLIENT_SECRET")
        )
        
        results = {}

        # 1. DEEP INVENTORY & NETWORKING (Reader Role)
        # We query VMs AND their attached Network Security Groups (NSGs).
        # This is vital for your "Diff-Calculator" to see current firewall rules.
        arg_client = ResourceGraphClient(creds)
        query = """
        Resources 
        | where type =~ 'Microsoft.Compute/virtualMachines' 
        | extend nsgId = tostring(properties.networkProfile.networkInterfaces[0].id)
        | project id, name, resourceGroup, location, vmSize=properties.hardwareProfile.vmSize, nsgId
        | limit 10
        """
        request = QueryRequest(subscriptions=[raw_sub_id], query=query)
        inventory_data = arg_client.resources(request)
        results["inventory"] = inventory_data.data

        print('Azure - Done Resoucre')

        # 2. SECURITY ALERTS & RECOMMENDATIONS (Security Reader Role)
        security_client = SecurityCenter(creds, raw_sub_id)
        
        # Alerts (Reactive)
        alerts = security_client.alerts.list()
        results["security_alerts"] = [
            {"title": a.alert_display_name, "severity": a.severity} 
            for i, a in enumerate(alerts) if i < 5 and a.state == "Active"
        ]

        print('Azure - Done Security')

        # This shows "misconfigurations" which is great for BSI Risk mapping.
        # recs = security_client.assessments.list(raw_sub_id)
        # results["security_recommendations"] = [
        #     {"name": r.display_name, "status": r.status.code}
        #     for i, r in enumerate(recs) if i < 5 and r.status.code == "Unhealthy"
        # ]

        # print('Azure - Done Assessments')

        # 3. PERFORMANCE METRICS (Monitoring Reader Role)
        # Fetching CPU Percentage for the first VM found to show "Activity State"
        if results["inventory"]:
            monitor_client = MonitorManagementClient(creds, raw_sub_id)
            target_vm_id = inventory_data.data[0]['id']
            timespan = f"{(datetime.utcnow() - timedelta(hours=1)).isoformat()}/{datetime.utcnow().isoformat()}"
            
            metrics = monitor_client.metrics.list(
                target_vm_id,
                timespan=timespan,
                interval='PT1H',
                metricnames='Percentage CPU,Network In,Network Out,Disk Read Bytes',
                aggregation='Average'
            )
            
            results["performance_sample"] = []
            for metric in metrics.value:
                for timeseries in metric.timeseries:
                    for data_point in timeseries.data:
                        results["performance_sample"].append({
                            "vm": inventory_data.data[0]['name'],
                            "cpu_avg": data_point.average
                        })

        print('Azure - Done Performance Metrics')

        # 4. BILLING DATA (Cost Management Reader Role)
        consumption_client = ConsumptionManagementClient(creds, raw_sub_id)
        usage = consumption_client.usage_details.list(scope=f"/subscriptions/{raw_sub_id}")
        results["billing_sample"] = [item.as_dict() for i, item in enumerate(usage) if i < 5]

        print('Azure - Done Billing')

        return {"azure_full_state": results}

    except Exception as e:
        return {"error": f"Azure fetch failed: {str(e)}"}