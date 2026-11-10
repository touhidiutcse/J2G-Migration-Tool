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

# DYNAMIC CREDENTIALS: Pull values securely from your environment
GITHUB_TOKEN = os.getenv("JIRA_MIGRATION_GITHUB_ACCESS_TOKEN")
ORG = os.getenv("JIRA_MIGRATION_GITHUB_NAME") or  "enosisbd"
REPO = os.getenv("JIRA_MIGRATION_GITHUB_REPO")

# Fast safety check to prevent empty API crashes
if not GITHUB_TOKEN:
    print("Error: Missing GITHUB_ACCESS_TOKEN in your .env file!")
    exit(1)

if not REPO:
    print("Error: Missing GITHUB_REPO in your .env file!")
    exit(1)

headers = {
    'Accept': 'application/vnd.github+json',
    'Authorization': f'token {GITHUB_TOKEN}'
}

# Dedicated repository issue-types endpoint
url = f"https://api.github.com/repos/{ORG}/{REPO}/issue-types"
print(f"Fetching issue types from: {url}...")

try:
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        raw_data = response.json()

        print("\n" + "="*95)
        print(f"ACTIVE GITHUB REPOSITORY ISSUE TYPES")
        print("="*95)

        # Beautifully aligned loop formatting
        print(f"{'ISSUE TYPE':<12} | {'ID':<10} | {'COLOR':<8} | {'NODE ID':<22} | {'DESCRIPTION'}")
        print("-" * 95)

        for item in raw_data:
            name = item.get('name', 'N/A')
            id_val = item.get('id', 'N/A')
            color = item.get('color', 'N/A')
            node_id = item.get('node_id', 'N/A')
            desc = item.get('description', 'No description provided')
            
            print(f"{name:<12} | {str(id_val):<10} | {color:<8} | {node_id:<22} | {desc}")

        print("="*95 + "\n")
    else:
        print(f"API Error: Received status code {response.status_code}")
        print(response.text)

except Exception as e:
    print(f"Network Error: Failed to connect to GitHub API: {e}")

'''
===============================================================================================
ISSUE TYPE   | ID         | COLOR    | NODE ID                | DESCRIPTION
-----------------------------------------------------------------------------------------------
Task         | 34500622   | yellow   | IT_kwDOEaYG-s4CDnAO    | A specific piece of work
Bug          | 34500623   | red      | IT_kwDOEaYG-s4CDnAP    | An unexpected problem or behavior
Feature      | 34500624   | blue     | IT_kwDOEaYG-s4CDnAQ    | A request, idea, or new functionality
===============================================================================================
'''

'''
# New Code
url = "https://api.github.com/repos/{org}/{repo}/issues/16"
raw_data = response.json()

# 1. Handle if GitHub wrapped the response inside a list array
if isinstance(raw_data, list):
    if len(raw_data) > 0:
        issue_data = raw_data[0]
    else:
        print("The API returned an empty list. Issue #16 might not exist or token lacks access.")
        issue_data = {}
else:
    issue_data = raw_data

# 2. Safely extract and inspect properties
if issue_data:
    # Print the specific keys we want to verify
    print("\n--- Issue Metadata Verification ---")
    print("Issue Title:   ", issue_data.get("title"))
    print("Native Type:   ", issue_data.get("issue_type"))
    print("Native Type ID:", issue_data.get("issue_type_id"))
    
    # Optional: If you want to see everything GitHub saved under the hood,
    # uncomment the line below to dump the full payload structure:
    print("\nFull Payload:\n", json.dumps(issue_data, indent=2))
else:
    print(f"Failed to fetch data properly. Status Code: {response.status_code}")
'''
    
'''
if response.status_code == 200:
    issue_types = response.json()
    print("\n--- Available Issue Types ---")
    for itype in issue_types:
        print(f"Type Name: {itype.get('name')} | ID: {itype.get('id') or 'Native System Type'}")
else:
    print(f"Failed to fetch issue types: {response.status_code} - {response.text}")
'''
'''
Type Name: Task | ID: 34242157

Type Name: Bug | ID: 34242158

Type Name: Feature | ID: 34242159

Type Name: Epic | ID: 34915665 


'''