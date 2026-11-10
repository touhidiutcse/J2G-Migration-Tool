#!/usr/bin/env python3

# /// script
# dependencies = [
#     "google-api-python-client",
#     "google-auth-httplib2",
#     "google-auth-oauthlib",
#     "requests",
#     "python-dateutil",
#     "lxml",
#     "python-dotenv",
# ]
# ///

from collections import namedtuple
from datetime import datetime
import os
import sys
import logging

from dotenv import load_dotenv

# Load configuration keys out of the .env file right at startup
load_dotenv()

# =====================================================================
# AUTOMATION PIPELINE: Pre-flight User Identity Extraction Scan
# =====================================================================
print("=====================================================================")
print("Step 1/2: Auto-executing User Extraction Scout (Building Cache)...")
print("=====================================================================")

try:
    # Dynamically imports your extract_users.py file
    import extract_users
    
    # If extract_users handles execution inside a main() function, invoke it cleanly
    if hasattr(extract_users, 'main'):
        extract_users.main()
        
    print("Identity Mapping Complete: 'jira_user_cache.json' is locked and ready.")
except Exception as e:
    print(f"Warning: Pre-flight user auto-extraction encountered an issue: {e}")
    print("Continuing migration pipeline using pre-existing local data cache...")

print("=====================================================================")
print("Step 2/2: Launching Core Migration Engine Pipeline Sequence...")
print("=====================================================================\n")

print("Press [ENTER] to proceed forward or Press [CTRL+C] to Cancel")

# All internal module imports grouped cleanly at the top
from project import Project
from importer import Importer
from labelcolourselector import LabelColourSelector
from utils import read_xml_files

# --- Configure Logging into a 'logs' Folder ---
log_dir = "logs"

# Automatically create the 'logs' folder if it doesn't exist yet
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# Construct the file path inside the folder: logs/migration_YYYYMMDD_HHMMSS.log
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
log_filename = os.path.join(log_dir, f"migration_{timestamp}.log")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_filename, mode="w", encoding="utf-8")
    ]
)

# --- User Inputs & Environment Setup ---
file_names = os.getenv('JIRA_MIGRATION_FILE_PATHS') or input(
    'Path to Jira XML query file (semi-colon separate for multiple files, directories are accepted): ')
all_xml_files = read_xml_files(file_names)

jira_proj = os.getenv('JIRA_MIGRATION_JIRA_PROJECT_NAME') or input('Jira project name: ')
jira_done_id = os.getenv('JIRA_MIGRATION_JIRA_DONE_ID') or input('Jira Done status Category ID [default "3" - Todo: 1, In Progress: 2]: ') or '3'
jira_base_url = os.getenv('JIRA_MIGRATION_JIRA_URL') or input('Jira base url [default "https://enosisbd.atlassian.net"]: ') or 'https://enosisbd.atlassian.net'

ac = os.getenv('JIRA_MIGRATION_GITHUB_NAME') or input('GitHub account name (user/org): ')
repo = os.getenv('JIRA_MIGRATION_GITHUB_REPO') or input('GitHub repository name: ')
pat = os.getenv('JIRA_MIGRATION_GITHUB_ACCESS_TOKEN') or input('Github Personal Access Token: ')

# --- Core variables pulled from .env or fallback prompts ---
jira_email = os.getenv('JIRA_EMAIL') or input('Jira User Account Email: ')
jira_api_token = os.getenv('JIRA_API_TOKEN') or input('Jira Personal API Token: ')
drive_folder_id = os.getenv('GOOGLE_DRIVE_FOLDER_ID') or input('Target Google Drive Folder ID: ')

# Fetch custom project field settings natively out of .env with defaults
gh_priority_id = os.getenv('GITHUB_FIELD_PRIORITY_ID') or input('GitHub Priority Field ID: ')
gh_estimate_id = os.getenv('GITHUB_FIELD_ESTIMATE_ID') or input('GitHub Estimate Field ID: ')
apply_issue_type = os.getenv('GITHUB_APPLY_NATIVE_ISSUE_TYPE', 'true').strip().lower() == 'true'

try:
    start_from_issue = input('Start from [default "0" (beginning)]: \n') or '0'

except KeyboardInterrupt:
    sys.stderr.write('\n[INFO] Migration cancelled by user. Exiting cleanly.\n')
    sys.exit(0)

# Update the running script NamedTuple mapping configurations so importer.py can reference them safely

Options = namedtuple("Options", "accesstoken account repo jira_email jira_api_token drive_folder_id gh_priority_id gh_estimate_id apply_issue_type")

opts = Options(
    accesstoken=pat, 
    account=ac, 
    repo=repo,
    jira_email=jira_email,
    jira_api_token=jira_api_token,
    drive_folder_id=drive_folder_id,
    gh_priority_id=int(gh_priority_id),      # Coerce directly to integer format
    gh_estimate_id=int(gh_estimate_id),      # Coerce directly to integer format
    apply_issue_type=apply_issue_type
)

project = Project(jira_proj, jira_done_id, jira_base_url)

# --- Processing XML ---
logging.info("--- Parsing XML Files ---")
total_items = 0

for f in all_xml_files:
    for item in f.channel.item:
        project.add_item(item)
        total_items += 1

logging.info(f"Loaded {total_items} items into memory successfully.")

project.prettify()

try:
    # This input stays interactive via terminal so you can manually trigger start
    input('Press Enter to begin...')
except KeyboardInterrupt:
    sys.stderr.write('\n[INFO] Migration cancelled by user. Exiting cleanly.\n')
    sys.exit(0)

sys.stderr.write('\n>>> Import started ...')
sys.stderr.flush()

logging.info(">>> Import started ...")

'''
Steps:
  1. Create any milestones
  2. Create any labels
  3. Create each issue with comments, linking them to milestones and labels
  4: Post-process all comments to replace issue id placeholders with the real ones
'''
importer = Importer(opts, project)
colourSelector = LabelColourSelector(project)

logging.info("[1/3] Importing Milestones...")
importer.import_milestones()

if int(start_from_issue) == 0:
    logging.info("[2/3] Importing Labels...")
    importer.import_labels(colourSelector)

logging.info("[3/3] Importing Issues and Comments...")
# Dynamically references the real timestamped file name instead of a hardcoded string
logging.info(f"Note: If the script stops, look at jira-keys-to-github-id.txt to see the last successfully imported issue number.\n")

# --- Execution ---
try:
    importer.import_issues(int(start_from_issue))

    #Step 4/4: Reconcile native parent/sub-issues pass
    logging.info("[4/4] Reconciling native GitHub sub-issues...")
    importer.reconcile_sub_issues()

except KeyboardInterrupt:
    logging.info("Process paused/interrupted by user via CMD. Exiting cleanly.")
    sys.exit(0)
except Exception as e:
    logging.error(f"An unexpected error occurred during execution: {e}")
    raise e

logging.info("Migration script task sequence completed!")