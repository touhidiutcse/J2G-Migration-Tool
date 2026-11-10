#!/usr/bin/env python3

# /// script
# dependencies = [
#     "requests",
#     "python-dotenv",
# ]
# ///

import os
import requests
from dotenv import load_dotenv

# Load credentials from your root .env file
load_dotenv()

# Pull values securely from your environment
GITHUB_TOKEN = os.getenv("JIRA_MIGRATION_GITHUB_ACCESS_TOKEN")
ORG = os.getenv("JIRA_MIGRATION_GITHUB_NAME") or  "enosisbd"

# Fast safety check to prevent empty API crashes
if not GITHUB_TOKEN:
    print("[ERROR] Missing GITHUB_ACCESS_TOKEN in your .env file!")
    exit(1)

headers = {
    'Accept': 'application/vnd.github+json',
    'Authorization': f'token {GITHUB_TOKEN}'
}

url = f"https://api.github.com/orgs/{ORG}/issue-fields"
print(f"[INFO] Fetching organization issue fields from: {url}...")

try:
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        fields = response.json()
        
        print("\n" + "="*80)
        print(f"ACTIVE GITHUB ORG ({ORG}) ISSUE FIELDS")
        print("="*80)
        
        # Beautifully aligned loop formatting
        print(f"{'FIELD NAME':<35} | {'FIELD ID'}")
        print("-" * 80)
        
        for field in fields:
            name = field.get('name', 'N/A')
            field_id = field.get('id', 'N/A')
            print(f"{name:<35} | {field_id}")
            
        print("="*80 + "\n")
    else:
        print(f"[ERROR] Failed to fetch fields: {response.status_code} - {response.text}")

except Exception as e:
    print(f"[ERROR] Network Error: Failed to connect to GitHub API: {e}")

'''
Field Name: Priority | ID: 43472901
Field Name: Start date | ID: 43472902
Field Name: Target date | ID: 43472903
Field Name: Effort | ID: 43472904
'''

'''
Field Name: Priority | ID: 43131176
Field Name: Start date | ID: 43131177
Field Name: Target date | ID: 43131178
Field Name: Effort | ID: 43131179
Field Name: Estimate | ID: 44023181
'''