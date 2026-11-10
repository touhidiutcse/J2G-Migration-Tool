#!/usr/bin/env python3

# /// script
# dependencies = [
#     "requests",
#     "lxml",
#     "python-dotenv",
# ]
# ///

import os
import re
import json
import requests
from lxml import etree
from dotenv import load_dotenv

# Load credentials from your .env file
load_dotenv()

JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
XML_PATH = os.getenv("JIRA_MIGRATION_FILE_PATHS") or "Jira.xml"

# DYNAMIC URL: Fetch from environment, strip trailing slash, and fallback safely
JIRA_BASE_URL = os.getenv("JIRA_BASE_URL") or "https://enosisbd.atlassian.net"
JIRA_BASE_URL = JIRA_BASE_URL.rstrip("/")

if not JIRA_EMAIL or not JIRA_API_TOKEN:
    print("Error: Missing JIRA_EMAIL or JIRA_API_TOKEN in your .env file!")
    exit(1)

if not os.path.exists(XML_PATH):
    print(f"Error: Could not find XML file at '{XML_PATH}'")
    exit(1)

print(f"Step 1: Scanning '{XML_PATH}' for Atlassian Account ID hashes...")

# --- FIX: Match standard hex hashes (24 chars) OR Atlassian colon-joined GUIDs ---
hash_pattern = re.compile(r'\b[a-f0-9]{24}\b|\b\d{6}:[a-f0-9-]{36}\b')
unique_hashes = set()

# Parse XML to find comment authors, reporters, and assignees
parser = etree.XMLParser(recover=True)
tree = etree.parse(XML_PATH, parser)
root = tree.getroot()

# Scan comments
for comment in root.findall(".//comment"):
    author = comment.get("author")
    if author and hash_pattern.match(author):
        unique_hashes.add(author)

# Scan assignee and reporter nodes
for node in ["assignee", "reporter"]:
    for element in root.findall(f".//{node}"):
        account_id = element.get("accountid")
        if account_id and hash_pattern.match(account_id):
            unique_hashes.add(account_id)

print(f"Found {len(unique_hashes)} unique user hashes to process.\n")
print("Step 2: Querying Jira API to resolve identities...")

user_cache = {}
cache_file = "jira_user_cache.json"

# Base headers for Atlassian API v3
headers = {"Accept": "application/json"}
auth = (JIRA_EMAIL, JIRA_API_TOKEN)

for idx, account_id in enumerate(unique_hashes, 1):
    print(f"[{idx}/{len(unique_hashes)}] Fetching data for hash: {account_id}...")
    
    # Atlassian Cloud REST URL to get user by accountId
    # url = f"https://enosisbd.atlassian.net/rest/api/3/user?accountId={account_id}"
    
    # DYNAMIC URL ROUTING: Clean API string generation
    url = f"{JIRA_BASE_URL}/rest/api/3/user?accountId={account_id}"

    try:
        response = requests.get(url, headers=headers, auth=auth, timeout=10)
        if response.status_code == 200:
            user_data = response.json()
            display_name = user_data.get("displayName", "Unknown User")
            email = user_data.get("emailAddress")  # Returns None if key doesn't exist
            
            # CLEAN METADATA RULE: Skip email parentheses entirely if no email is found
            if email and email.strip():
                user_string = f"{display_name} [{email.lower()}]"
            else:
                user_string = display_name
            
            # Now save user_string directly to your phonebook dictionary/JSON file
            
            formatted_identity = f"{display_name} (<{email}>)"
            user_cache[account_id] = formatted_identity
            print(f"Mapped successfully to: {display_name} ({email})")
        else:
            print(f"API returned error status: {response.status_code} - {response.text}")
            user_cache[account_id] = f"**Jira User ({account_id[:8]})**"
    except Exception as e:
        print(f" Network error: {e}")
        user_cache[account_id] = f"**Jira User ({account_id[:8]})**"

# Save the final results to a local JSON file
with open(cache_file, "w", encoding="utf-8") as f:
    json.dump(user_cache, f, indent=4)

print(f"\nSuccess! All resolved users saved locally to '{cache_file}'")

# uv run --with python-dotenv extract_users.py