import os

def fetch_aws():
    import boto3
    try:
        session = boto3.Session(
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION")
        )
        ec2 = session.client('ec2')
        response = ec2.describe_instances()
        instances = []
        for reservation in response['Reservations']:
            for inst in reservation['Instances']:
                instances.append({
                    "InstanceId": inst['InstanceId'],
                    "Type": inst['InstanceType'],
                    "State": inst['State']['Name']
                })
        return {"aws_inventory": instances}
    except Exception as e:
        return {"error": f"AWS fetch failed: {str(e)}"}