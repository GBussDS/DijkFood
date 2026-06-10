import boto3
from datetime import datetime, timedelta

logs = boto3.client("logs", region_name="us-east-1")
log_group = "/ecs/dijkfood/dijkfood-conversational"

try:
    streams = logs.describe_log_streams(
        logGroupName=log_group,
        orderBy='LastEventTime',
        descending=True,
        limit=2
    )
    
    for stream in streams.get('logStreams', []):
        print(f"\n--- Lendo logs do stream: {stream['logStreamName']} ---")
        events = logs.get_log_events(
            logGroupName=log_group,
            logStreamName=stream['logStreamName'],
            limit=50,
            startFromHead=False
        )
        for event in events['events']:
            print(event['message'])
except Exception as e:
    print(f"Erro ao ler logs: {e}")
