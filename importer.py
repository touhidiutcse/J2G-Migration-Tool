import os
import io
import requests
import time
import re
import json

from utils import fetch_labels_mapping, fetch_allowed_labels, convert_label
from config import JIRA_TYPE_TO_GITHUB, JIRA_PRIORITY_TO_GITHUB

# --- GOOGLE DRIVE INTEGRATION MODULE ---
import tempfile
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

SCOPES = ['https://www.googleapis.com/auth/drive.file']

# Resolve file paths and folders dynamically from environment
CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_FILE") or "credentials.json"
TOKEN_PATH = os.getenv("GOOGLE_TOKEN_FILE") or "token.json"
TARGET_FOLDER_ID = os.getenv("GOOGLE_DRIVE_TARGET_FOLDER_ID")

def get_drive_service():
    """Authenticates using credentials.json and handles OAuth token tracking."""
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, 'w') as token:
            token.write(creds.to_json())
    return build('drive', 'v3', credentials=creds)


def create_drive_subfolder(service, folder_name, parent_folder_id):
    """
    Creates a subfolder (e.g., 'HOM-150') inside the master migration folder
    if it doesn't exist yet, and returns its unique Google Drive ID.
    Supports both personal drives and Shared Drives.
    """
    # Search if this ticket folder was already created during a previous run attempt
    query = f"name = '{folder_name}' and '{parent_folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    
    # ADDED: supportsAllDrives & includeItemsFromAllDrives to enable Shared Drive searching
    results = service.files().list(
        q=query, 
        fields="files(id)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    files = results.get('files', [])
    
    if files:
        return files[0]['id'] # Return existing folder ID seamlessly
        
    # If it doesn't exist, create it cleanly inside Google Drive
    folder_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [parent_folder_id]
    }
    
    # ADDED: supportsAllDrives=True
    folder = service.files().create(
        body=folder_metadata, 
        fields='id',
        supportsAllDrives=True
    ).execute()
    return folder.get('id')


def upload_to_drive(service, file_name, jira_response, mime_type, folder_id=None):
    """
    Downloads a Jira stream to a temporary local file and uploads it to Google Drive 
    using 10MB chunks to solve the 'seek' error while keeping RAM usage at zero.
    Supports both personal drives and Shared Drives.
    """
    file_metadata = {'name': file_name}
    if folder_id:
        file_metadata['parents'] = [folder_id]

    # Create a temporary file on your hard drive that deletes itself when closed
    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
        temp_file_path = temp_file.name
        print(f"Streaming '{file_name}' from network to local cache...")
        
        # Download from Jira in 1MB network increments to the temp file
        for chunk in jira_response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                temp_file.write(chunk)
                
    # Open the local file back up for Google. This supports full seek operations!
    with open(temp_file_path, 'rb') as local_file:
        media = MediaIoBaseUpload(local_file, mimetype=mime_type, chunksize=10 * 1024 * 1024, resumable=True)
        
        print(f"Initializing chunked upload to Google Drive...")
        
        # ADDED: supportsAllDrives=True
        request = service.files().create(
            body=file_metadata, 
            media_body=media, 
            fields='id, webViewLink',
            supportsAllDrives=True
        )
        
        file = None
        while file is None:
            status, file = request.next_chunk()
            if status:
                print(f" Upload progress: {int(status.progress() * 100)}%")

    # Clean up and delete the temporary file from your computer
    try:
        os.remove(temp_file_path)
    except Exception:
        pass

    file_id = file.get('id')

    # Security: Lock file access to the specified Google Workspace domain
    workspace_domain = os.getenv("GOOGLE_WORKSPACE_DOMAIN", "enosisbd.com")

    # Security: Domain lock to enosisbd.com matches your environment variables
    # ADDED: supportsAllDrives=True (Required when updating permissions on files stored in Shared Drives)
    domain_permission = {
        'type': 'domain',
        'role': 'reader',
        'domain': workspace_domain 
    }
    try:
        service.permissions().create(
            fileId=file_id, 
            body=domain_permission,
            supportsAllDrives=True
        ).execute()
    except Exception as e:
        # Catch case where Shared Drive policies restrict domain-level permission overwrites
        print(f"Warning: Could not set domain permission on file (Inherited from Shared Drive instead): {e}")

    return file.get('webViewLink')

class Importer:
    _GITHUB_ISSUE_PREFIX = "INFRA-"
    _PLACEHOLDER_PREFIX = "@PSTART"
    _PLACEHOLDER_SUFFIX = "@PEND"
    _DEFAULT_TIME_OUT = 120.0

    def __init__(self, options, project):
        self.options = options
        self.project = project
        self.github_url = 'https://api.github.com/repos/%s/%s' % (
            self.options.account, self.options.repo)
        '''
        self.jira_issue_replace_patterns = {
            'https://issues.jenkins.io/browse/%s%s' % (self.project.name, r'-(\d+)'): r'\1',
            self.project.name + r'-(\d+)': Importer._GITHUB_ISSUE_PREFIX + r'\1',
            r'Issue (\d+)': Importer._GITHUB_ISSUE_PREFIX + r'\1'}
        '''
        self.headers = {
            'Accept': 'application/vnd.github.golden-comet-preview+json',
            'Authorization': f'token {options.accesstoken}'
        }

        self.labels_mapping = fetch_labels_mapping()
        self.approved_labels = fetch_allowed_labels()

    def _resolve_jira_user(self, account_id):
        """
        Dynamically looks up an Atlassian Account ID hash over the Jira API,
        fetches their real name/email, and caches it locally to maximize speed.
        """
        cache_file = "jira_user_cache.json"
        
        if os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                cache = json.load(f)
        else:
            cache = {}

        if account_id in cache:
            return cache[account_id]

        print(f"First time seeing user hash {account_id}. Fetching identity from Jira Cloud...")
        # url = f"https://enosisbd.atlassian.net/rest/api/3/user?accountId={account_id}"

        # DYNAMIC URL: Fetch from environment, strip trailing slash, and fallback safely
        jira_base_url = os.getenv("JIRA_BASE_URL") or "https://enosisbd.atlassian.net"
        jira_base_url = jira_base_url.rstrip("/")
        
        url = f"{jira_base_url}/rest/api/3/user?accountId={account_id}"

        headers = {"Accept": "application/json"}
        auth = (self.options.jira_email, self.options.jira_api_token)

        try:
            r = requests.get(url, headers=headers, auth=auth, timeout=10)
            if r.status_code == 200:
                user_data = r.json()
                
                # ... API request & JSON loading ...
                display_name = user_data.get("displayName", "Unknown User")
                raw_email = user_data.get("emailAddress") or ""
                
                # Clean and sanitize the email string
                email = raw_email.strip().lower()
                
                # DEFENSIVE CHECK: Is the email empty or a privacy placeholder?
                is_valid_email = email and email not in ["none", "<none>", "null", "undefined", "<>"]

                if is_valid_email:
                    formatted_identity = f"{display_name} (<{email}>)"
                else:
                    formatted_identity = display_name
                
                cache[account_id] = formatted_identity
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(cache, f, indent=4)
                
                return formatted_identity
        except Exception as e:
            print(f"Failed to resolve Jira user over network: {e}")

        return f"Jira User ({account_id[:8]})"

    def import_milestones(self):
        """
        Imports the gathered project milestones into GitHub and remembers the created milestone ids
        """
        milestone_url = self.github_url + '/milestones'
        print('Importing milestones...', milestone_url)
        print

        # Check existing first
        existing = list()

        def get_milestone_list(url):
            return requests.get(url, headers=self.headers,
                                timeout=Importer._DEFAULT_TIME_OUT)

        def get_next_page_url(url):
            return url.replace('<', '').replace('>', '').replace('; rel="next"', '')

        milestone_pages = list()
        ms = get_milestone_list(milestone_url + '?state=all')
        milestone_pages.append(ms.json())

        if 'Link' in ms.headers:
            links = ms.headers['Link'].split(',')
            nextPageUrl = get_next_page_url(links[0])

            while nextPageUrl is not None:
                time.sleep(1)
                nextPageUrl = None

                for l in links:
                    if 'rel="next"' in l:
                        nextPageUrl = get_next_page_url(l)

                if nextPageUrl is not None:
                    ms = get_milestone_list(nextPageUrl)
                    links = ms.headers['Link'].split(',')
                    milestone_pages.append(ms.json())

        for ms_json in milestone_pages:
            for m in ms_json:
                print(self.project.get_milestones().keys())
                try:
                    if m['title'] in self.project.get_milestones().keys():
                        self.project.get_milestones()[m['title']] = m['number']
                        print(m['title'], 'found')
                        existing.append(m['title'])
                except TypeError:
                    pass

        # Export new ones
        for mkey in self.project.get_milestones().keys():
            if mkey in existing:
                continue

            data = {'title': mkey}
            r = requests.post(milestone_url, json=data, headers=self.headers,
                timeout=Importer._DEFAULT_TIME_OUT)

            # overwrite histogram data with the actual milestone id now
            if r.status_code == 201:
                content = r.json()
                self.project.get_milestones()[mkey] = content['number']
                print(mkey)


    def import_labels(self, colour_selector):
        """
        Imports the gathered project components and labels as labels into GitHub 
        """
        label_url = self.github_url + '/labels'
        print('Importing labels...', label_url)
        print()

        for lkey in self.project.get_all_labels().keys():
            if lkey is None or str(lkey).strip() == '' or str(lkey).lower() == 'none':
                continue

            prefixed_lkey = lkey.lower()
            print(f"lkey is: {lkey}")
            
            if os.getenv('JIRA_MIGRATION_INCLUDE_COMPONENT_IN_LABELS', 'true') == 'true':
                if lkey in self.project.get_components().keys():
                    prefixed_lkey = 'jira-component:' + prefixed_lkey

            prefixed_lkey = convert_label(prefixed_lkey, self.labels_mapping, self.approved_labels)
            print(f"Prefixed lkey: {prefixed_lkey}")
            
            if prefixed_lkey is None or str(prefixed_lkey).strip() == '' or str(prefixed_lkey).lower() == 'none':
                print(f"Skipping unapproved, unmapped, or null label choice for: {lkey}")
                print("-" * 40)
                continue

            data = {'name': prefixed_lkey,
                    'color': colour_selector.get_colour(lkey)}
            print(f"Data: {data}")
            r = requests.post(label_url, json=data, headers=self.headers, timeout=Importer._DEFAULT_TIME_OUT)
            if r.status_code == 201:
                print(lkey + '->' + str(prefixed_lkey))
            elif r.status_code == 422 and "already_exists" in r.text:
                print(f"Label '{prefixed_lkey}' already exists in repository. Skipping smoothly.")
            else:
                print('Failure importing label ' + str(prefixed_lkey),
                      r.status_code, r.content, r.headers)

    def import_issues(self, start_from_count):
        print('Importing issues...')
        count = 0
        summary = {'imported': 0, 'skipped': 0, 'failed': 0}

        for issue in self.project.get_issues():
            if start_from_count > count:
                count += 1
                summary['skipped'] += 1
                continue

            print("Index = ", count)

            if 'milestone_name' in issue:
                issue['milestone'] = self.project.get_milestones()[
                    issue['milestone_name']]
                del issue['milestone_name']

            self.convert_relationships_to_comments(issue)

            # Ensure 'attachments' exists in the issue dictionary if the parser skipped it
            if 'attachments' not in issue:
                issue['attachments'] = []

            # OPTIMIZED MAP CACHE: Pre-load existing mappings to guarantee instant O(1) lookups
            parent_id_map = {}
            if os.path.exists('jira-keys-to-github-id.txt'):
                try:
                    with open('jira-keys-to-github-id.txt', 'r', encoding='utf-8') as f:
                        for line in f:
                            if ':' in line:
                                k, v = line.strip().split(':', 1)
                                parent_id_map[k.strip()] = v.strip()
                except Exception:
                    pass

            issue_comments = issue['comments']
            del issue['comments']
            comments = []

            for comment in issue_comments:
                cleaned_comment = dict((k, self._replace_jira_with_github_id(v)) for k, v in comment.items())
                
                # --- HTML-AWARE USER RESOLVER & PARENT TASK RE-ROUTER ---
                if 'body' in cleaned_comment:
                    body_text = cleaned_comment['body']
                    
                    # 1. First Pass: Resolve our custom Parent Task tracking tokens if present
                    if "TRACK_PARENT_KEY_PLACEHOLDER:" in body_text:
                        try:
                            raw_placeholder = body_text.split("TRACK_PARENT_KEY_PLACEHOLDER:")[1]
                            
                            # Split using our unique multi-character delimiter to safeguard against raw text characters
                            parent_key, parent_title = raw_placeholder.split("###SPLIT###", 1)
                            parent_key = parent_key.strip()
                            parent_title = parent_title.strip()
                            
                            # Query our in-memory mapping cache
                            github_id = parent_id_map.get(parent_key)
                            title_suffix = f" - {parent_title}" if parent_title else ""
                            
                            if github_id:
                                body_text = f"Subtask of parent GitHub issue: #{github_id}{title_suffix}"
                            else:
                                jira_link = f"[{parent_key}]({self.project.jiraBaseUrl}/browse/{parent_key})"
                                body_text = f"Subtask of parent Jira task: {jira_link}{title_suffix}"
                        except Exception as parse_error:
                            print(f"⚠️ Safe Skip: Couldn't reconstruct parent link string: {parse_error}")
                            pass

                    # 2. Second Pass: Run your existing profile anchor link replacement engine
                    profile_link_pattern = re.compile(
                        r'(<i>)?<a\s+[^>]*?href="[^"]*?accountId=([^"&>]+)[^"]*"[^>]*?>(.*?)</a>(\s*:?\s*</i>)?', 
                        re.IGNORECASE
                    )

                    def link_replacer(match):
                        is_header = match.group(1) is not None  # Header validation
                        account_id = match.group(2).strip()     # Extracted Atlassian account hash
                        display_text = match.group(3)           # Fallback text inside link
                        
                        # Fetch true identity via the account_id from cache
                        resolved_name = display_text
                        if hasattr(self, 'project') and hasattr(self.project, 'users') and self.project.users:
                            resolved_name = self.project.users.get(account_id, display_text)
                        elif hasattr(self, '_resolve_jira_user'):
                            resolved_name = self._resolve_jira_user(account_id)
                        
                        # Clean up formatting leftovers and bracket edge-cases completely
                        clean_text = str(resolved_name).replace('**', '').strip()
                        clean_text = clean_text.replace('(<None>)', '').replace('(None)', '')
                        clean_text = clean_text.replace('<None>', '').replace('None', '')
                        if clean_text.endswith('()') or clean_text.endswith('[]'):
                            clean_text = clean_text[:-2].strip()
                        clean_text = clean_text.replace('()', '').replace('[]', '').strip()
                        
                        if is_header:
                            return f'<b>{clean_text} commented</b>:'
                        else:
                            return f'<b>{clean_text}</b>'

                    body_text = profile_link_pattern.sub(link_replacer, body_text)
                    
                    # 3. Third Pass: Clean up remaining loose text hashes
                    hash_pattern = re.compile(r'\b[a-f0-9]{24}\b|\b\d{6}:[a-f0-9-]{36}\b')
                    
                    def raw_hash_replacer(match):
                        found_hash = match.group(0)
                        start_pos = match.start()
                        lookbehind = body_text[max(0, start_pos - 60):start_pos].lower()
                        if "attachment" in lookbehind or "content" in lookbehind or "stream" in lookbehind:
                            return found_hash
                            
                        return self._resolve_jira_user(found_hash)
                        
                    body_text = hash_pattern.sub(raw_hash_replacer, body_text)
                    cleaned_comment['body'] = body_text
                # --------------------------------------------------
                
                comments.append(cleaned_comment)

            try:
                self.import_issue_with_comments(issue, comments)
                summary['imported'] += 1
            except Exception as e:
                print(f"Error importing issue {issue.get('key', 'Unknown')}: {e}")
                summary['failed'] += 1

            count += 1

            # PRODUCTION SAFETY: Avoid triggering GitHub's secondary abuse rate limits
            print("Cooling down for 4 seconds to protect GitHub API limits...")
            time.sleep(4)

        # SUMMARY REPORT AT COMPLETION
        print(
            '\nIssue import complete: '
            f"imported={summary['imported']}, skipped={summary['skipped']}, "
            f"failed={summary['failed']}\n"
        )
        return summary

    def reconcile_sub_issues(self):
        """Create native GitHub parent/sub-issue relationships after import.

        Relationships are collected using Jira keys while XML is parsed. This
        post-import pass resolves both sides through the persisted Jira to
        GitHub mapping, so the order of issues in the XML does not matter.
        """
        relationships = self.project.get_parent_children()
        if not relationships:
            print('No Jira parent/sub-issue relationships found.')
            return {'created': 0, 'existing': 0, 'failed': 0, 'missing': 0}

        mapping = self._load_issue_mapping()
        if not mapping:
            print('Unable to reconcile sub-issues: no Jira/GitHub mapping file found.')
            return {'created': 0, 'existing': 0, 'failed': 0, 'missing': 0}

        issue_id_cache = {}
        summary = {'created': 0, 'existing': 0, 'failed': 0, 'missing': 0}

        for parent_key, child_keys in sorted(relationships.items()):
            parent_number = mapping.get(parent_key)
            if not parent_number:
                print(f"Missing GitHub mapping for parent Jira issue '{parent_key}'.")
                summary['missing'] += len(child_keys)
                continue

            existing = self._get_existing_sub_issue_identifiers(parent_number)
            if existing is None:
                summary['failed'] += len(child_keys)
                continue

            for child_key in sorted(child_keys):
                child_number = mapping.get(child_key)
                if not child_number:
                    print(f"Missing GitHub mapping for child Jira issue '{child_key}'.")
                    summary['missing'] += 1
                    continue

                child_id = issue_id_cache.get(child_number)
                if child_id is None:
                    child_id = self._get_github_issue_id(child_number)
                    if child_id is None:
                        summary['failed'] += 1
                        continue
                    issue_id_cache[child_number] = child_id

                if str(child_number) in existing or str(child_id) in existing:
                    print(f"Sub-issue already linked: {parent_key} -> {child_key}")
                    summary['existing'] += 1
                    continue

                sub_issue_url = f"{self.github_url}/issues/{parent_number}/sub_issues"
                response = requests.post(
                    sub_issue_url,
                    json={'sub_issue_id': child_id},
                    headers=self._github_json_headers(),
                    timeout=Importer._DEFAULT_TIME_OUT
                )

                if response.status_code in (200, 201):
                    print(f"Created GitHub sub-issue relationship: {parent_key} -> {child_key}")
                    existing.add(str(child_number))
                    existing.add(str(child_id))
                    summary['created'] += 1
                else:
                    print(
                        f"Failed to link GitHub sub-issue {child_key} under {parent_key}: "
                        f"{response.status_code} - {response.text}"
                    )
                    summary['failed'] += 1

        print(
            'Sub-issue reconciliation complete: '
            f"created={summary['created']}, existing={summary['existing']}, "
            f"missing={summary['missing']}, failed={summary['failed']}"
        )
        return summary

    def _load_issue_mapping(self):
        mapping = {}
        if not os.path.exists('jira-keys-to-github-id.txt'):
            return mapping

        try:
            with open('jira-keys-to-github-id.txt', 'r', encoding='utf-8') as mapping_file:
                for line in mapping_file:
                    if ':' not in line:
                        continue
                    jira_key, github_number = line.strip().split(':', 1)
                    if jira_key.strip() and github_number.strip():
                        mapping[jira_key.strip()] = github_number.strip()
        except OSError as error:
            print(f'Unable to read Jira/GitHub mapping file: {error}')

        return mapping

    def _github_json_headers(self):
        headers = dict(self.headers)
        headers['Accept'] = 'application/vnd.github+json'
        headers['X-GitHub-Api-Version'] = '2022-11-28'
        return headers

    def _get_github_issue_id(self, issue_number):
        issue_url = f"{self.github_url}/issues/{issue_number}"
        response = requests.get(
            issue_url,
            headers=self._github_json_headers(),
            timeout=Importer._DEFAULT_TIME_OUT
        )
        if response.status_code != 200:
            print(
                f"Failed to retrieve GitHub issue {issue_number}: "
                f"{response.status_code} - {response.text}"
            )
            return None

        issue_id = response.json().get('id')
        if issue_id is None:
            print(f"GitHub issue {issue_number} response did not contain an issue id.")
        return issue_id

    def _get_existing_sub_issue_identifiers(self, parent_number):
        identifiers = set()
        next_url = f"{self.github_url}/issues/{parent_number}/sub_issues?per_page=100"

        while next_url:
            response = requests.get(
                next_url,
                headers=self._github_json_headers(),
                timeout=Importer._DEFAULT_TIME_OUT
            )
            if response.status_code != 200:
                print(
                    f"Failed to retrieve sub-issues for GitHub issue {parent_number}: "
                    f"{response.status_code} - {response.text}"
                )
                return None

            for sub_issue in response.json():
                if sub_issue.get('id') is not None:
                    identifiers.add(str(sub_issue['id']))
                if sub_issue.get('number') is not None:
                    identifiers.add(str(sub_issue['number']))

            next_url = response.links.get('next', {}).get('url')

        return identifiers

    def import_issue_with_comments(self, issue, comments):
        print('Issue ', issue['key'])
        issue['labels'] = [label for label in issue['labels'] if label]
        print('Labels', issue['labels'])
        jira_key = issue['key']
        del issue['key']

        print(f"issues: {issue}")
        print(f"comments: {comments}")

        priority_value = issue.get('priority_field')
        type_value = issue.get('type_field')
        estimate_value = issue.get('estimate_field')

        # --- Handle Attachments Pipeline ---
        attachments = issue.get('attachments', [])
        if attachments:
            try:
                drive_service = get_drive_service()
                
                # Master folder ID mapping provided by options initialization
                MASTER_FOLDER_ID = self.options.drive_folder_id
                
                # Use the extracted jira_key (e.g., 'HOM-150') for the folder name
                folder_name = jira_key if jira_key else "Unknown-Ticket"
                print(f"Isolation Level: Managing subfolder '{folder_name}' inside Google Drive...")
                
                # Get or Create ticket-specific isolated directory
                TICKET_FOLDER_ID = create_drive_subfolder(drive_service, folder_name, MASTER_FOLDER_ID)
                
                attachment_markdown = "\n\n### 📎 Migrated Jira Attachments\n"
                
                for attach in attachments:
                    print(f"Downloading attachment '{attach['name']}' from Jira...")
                    
                    jira_download_headers = { 'Accept': '*/*' }
                    JIRA_EMAIL = self.options.jira_email
                    JIRA_API_TOKEN = self.options.jira_api_token
                    
                    jira_response = requests.get(
                        attach['url'], 
                        headers=jira_download_headers, 
                        auth=(JIRA_EMAIL, JIRA_API_TOKEN),
                        stream=True,
                        timeout=Importer._DEFAULT_TIME_OUT
                    )
                    
                    if jira_response.status_code == 200:
                        mime = jira_response.headers.get('Content-Type', 'application/octet-stream')
                        
                        drive_link = upload_to_drive(drive_service, attach['name'], jira_response, mime, TICKET_FOLDER_ID)
                        
                        # FIX: Remove the '!' prefix for images so GitHub Camo stops proxy-breaking the Drive links
                        attachment_markdown += f"- [{attach['name']}]({drive_link})\n"
                        print(f"Successfully uploaded to Drive: {drive_link}")

                    else:
                        print(f"Failed to download from Jira: {jira_response.status_code} - {jira_response.text}")
                
                issue['body'] += attachment_markdown
                
            except Exception as e:
                print(f"Warning: Could not process attachments due to Google Drive Auth/API error: {e}")
        
        # Clean up legacy properties before pushing to import REST schemas
        if 'attachments' in issue: del issue['attachments']
        if 'priority_field' in issue: del issue['priority_field']
        if 'type_field' in issue: del issue['type_field']
        if 'estimate_field' in issue: del issue['estimate_field']

        response = self.upload_github_issue(issue, comments)
        status_url = response.json()['url']
        gh_issue_url = self.wait_for_issue_creation(status_url).json()['issue_url']
        gh_issue_id = int(gh_issue_url.split('/')[-1])

        issue['githubid'] = gh_issue_id
        issue['key'] = jira_key

        if type_value:
            self.apply_native_issue_type(gh_issue_id, type_value)
            print("Waiting for GitHub layout schemas to update...")
            time.sleep(2)

        if priority_value:
            self.apply_native_priority(gh_issue_id, priority_value)
        if estimate_value:
            self.apply_native_estimate(gh_issue_id, estimate_value)

        jira_gh = f"{jira_key}:{gh_issue_id}\n"
        with open('jira-keys-to-github-id.txt', 'a') as f:
            f.write(jira_gh)

    def apply_native_priority(self, issue_number, jira_priority):
        if not jira_priority:
            return
        raw_priority = jira_priority.strip().lower()
        
        formatted_priority = JIRA_PRIORITY_TO_GITHUB.get(raw_priority, 'Medium')
        
        PRIORITY_FIELD_ID = self.options.gh_priority_id
        
        field_value_url = f"{self.github_url}/issues/{issue_number}/issue-field-values"
        
        payload = {
            "issue_field_values": [{"field_id": PRIORITY_FIELD_ID, "value": formatted_priority}]
        }
        headers = {
            'Accept': 'application/vnd.github+json',
            'Authorization': f'token {self.options.accesstoken}',
            'Content-Type': 'application/json'
        }
        print(f"Translating Jira '{jira_priority}' -> GitHub '{formatted_priority}' using field ID {PRIORITY_FIELD_ID}...")
        r = requests.post(field_value_url, json=payload, headers=headers, timeout=Importer._DEFAULT_TIME_OUT)
        if r.status_code in [200, 201]:
            print(f"Successfully applied native priority field value!")
        else:
            print(f"Failed to set native field priority: {r.status_code} - {r.text}")

    def apply_native_issue_type(self, issue_number, jira_type):
        if not self.options.apply_issue_type or not jira_type:
            return
            
        raw_type = jira_type.strip().lower()
        
        formatted_type = JIRA_TYPE_TO_GITHUB.get(raw_type, 'Task')

        issue_url = f"{self.github_url}/issues/{issue_number}"
        payload = {"type": formatted_type}
        headers = {
            'Accept': 'application/vnd.github+json',
            'Authorization': f'token {self.options.accesstoken}',
            'Content-Type': 'application/json'
        }
        print(f"Applying native GitHub Issue Type -> '{formatted_type}' for Issue #{issue_number}...")
        r = requests.patch(issue_url, json=payload, headers=headers, timeout=Importer._DEFAULT_TIME_OUT)
        if r.status_code == 200:
            print(f"Successfully applied native issue type via string mapping!")
        else:
            print(f"Failed to set native issue type: {r.status_code} - {r.text}")

    def apply_native_estimate(self, issue_number, jira_estimate):
        if not jira_estimate:
            return
        formatted_estimate = jira_estimate.strip()
        
        ESTIMATE_FIELD_ID = self.options.gh_estimate_id
        
        field_value_url = f"{self.github_url}/issues/{issue_number}/issue-field-values"
        payload = {
            "issue_field_values": [{"field_id": ESTIMATE_FIELD_ID, "value": formatted_estimate}]
        }
        headers = {
            'Accept': 'application/vnd.github+json',
            'Authorization': f'token {self.options.accesstoken}',
            'Content-Type': 'application/json'
        }
        print(f"Setting native Estimate field ID {ESTIMATE_FIELD_ID} to '{formatted_estimate}' for Issue #{issue_number}...")
        r = requests.post(field_value_url, json=payload, headers=headers, timeout=Importer._DEFAULT_TIME_OUT)
        if r.status_code in [200, 201]:
            print(f"Successfully applied native estimate field value!")
        else:
            print(f"Failed to set native estimate field: {r.status_code} - {r.text}")

    def upload_github_issue(self, issue, comments):
        issue_url = self.github_url + '/import/issues'
        issue_data = {'issue': issue, 'comments': comments}
        response = requests.post(issue_url, json=issue_data, headers=self.headers, 
            timeout=Importer._DEFAULT_TIME_OUT)
        if response.status_code == 202:
            return response
        elif response.status_code == 422:
            raise RuntimeError(
                "Initial import validation failed for issue '{}' due to the following errors:\n{}"
                .format(issue['title'], response.json())
            )
        else:
            raise RuntimeError(
                "Failed to POST issue: '{}' due to unexpected HTTP status code: {}\nerrors:\n{}"
                .format(issue['title'], response.status_code, response.json())
            )

    def wait_for_issue_creation(self, status_url):
        while True:
            time.sleep(3)
            response = requests.get(status_url, headers=self.headers, 
                timeout=Importer._DEFAULT_TIME_OUT)
            if response.status_code == 404:
                continue
            elif response.status_code != 200:
                raise RuntimeError(
                    "Failed to check GitHub issue import status url: {} due to unexpected HTTP status code: {}"
                    .format(status_url, response.status_code)
                )

            status = response.json()['status']
            if status != 'pending':
                break

        if status == 'imported':
            print("Imported Issue:", response.json()['issue_url'].replace('api.github.com/repos/', 'github.com/'))
        elif status == 'failed':
            raise RuntimeError(
                "Failed to import GitHub issue due to the following errors:\n{}"
                .format(response.json())
            )
        else:
            raise RuntimeError(
                "Status check for GitHub issue import returned unexpected status: '{}'"
                .format(status)
            )
        return response

    def convert_relationships_to_comments(self, issue):
        duplicates = issue['duplicates']
        is_duplicated_by = issue['is-duplicated-by']
        relates_to = issue['is-related-to']
        depends_on = issue['depends-on']
        blocks = issue['blocks']
        try:
            epic_link = issue['epic-link']
        except (AttributeError, KeyError):
            epic_link = None

        for duplicate_item in duplicates:
            issue['comments'].append(
                {"body": f'<i>[Duplicates: <a href="https://github.com/{self.options.account}/{self.options.repo}/issues?q=in%3Atitle%20' + self._replace_jira_with_github_id(duplicate_item) + '">' + self._replace_jira_with_github_id(duplicate_item) + '</a>]</i>'})

        for is_duplicated_by_item in is_duplicated_by:
            issue['comments'].append(
                {"body": f'<i>[Originally duplicated by: <a href="https://github.com/{self.options.account}/{self.options.repo}/issues?q=in%3Atitle%20' + self._replace_jira_with_github_id(is_duplicated_by_item) + '">' + self._replace_jira_with_github_id(is_duplicated_by_item) + '</a>]</i>'})

        for relates_to_item in relates_to:
            issue['comments'].append(
                {"body": f'<i>[Originally related to: <a href="https://github.com/{self.options.account}/{self.options.repo}/issues?q=in%3Atitle%20' + self._replace_jira_with_github_id(relates_to_item) + '">' + self._replace_jira_with_github_id(relates_to_item) + '</a>]</i>'})

        for depends_on_item in depends_on:
            issue['comments'].append(
                {"body": f'<i>[Originally depends on: <a href="https://github.com/{self.options.account}/{self.options.repo}/issues?q=in%3Atitle%20' + self._replace_jira_with_github_id(depends_on_item) + '">' + self._replace_jira_with_github_id(depends_on_item) + '</a>]</i>'})

        for blocks_item in blocks:
            issue['comments'].append(
                {"body": f'<i>[Originally blocks: <a href="https://github.com/{self.options.account}/{self.options.repo}/issues?q=in%3Atitle%20' + self._replace_jira_with_github_id(blocks_item) + '">' + self._replace_jira_with_github_id(blocks_item) + '</a>]</i>'})

        if epic_link:
            issue['comments'].append(
                {"body": f'<i>[Epic: <a href="https://github.com/{self.options.account}/{self.options.repo}/issues?q=in%3Atitle%20' + self._replace_jira_with_github_id(epic_link) + '%20label%3Aepic">' + self._replace_jira_with_github_id(epic_link) + '</a>]</i>'})

        del issue['duplicates']
        del issue['is-duplicated-by']
        del issue['is-related-to']
        del issue['depends-on']
        del issue['blocks']
        try:
            del issue['epic-link']
        except KeyError:
            pass

    def _replace_jira_with_github_id(self, text):
        return text

    def _replace_github_id_placeholder(self, text):
        return text