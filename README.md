# J2G: Jira Cloud to GitHub Migration Engine

Python 3.x scripts for importing Jira Cloud issues in XML format into an existing GitHub project without existing issues.

---

## Features

* Import Jira milestones as GitHub milestones.
* Import Jira labels as GitHub labels.
* Import Jira components as GitHub labels.
* Import Jira issues as GitHub issues where:
  * Issue IDs are mapped, e.g., `HOM-1` becomes GitHub Issue `#1` and `HOM-4711` becomes GitHub Issue `#4711`.
  * Both issue label and component assignments are mapped to GitHub labels.
  * Issue relationships like "depends on", "blocks", or "duplicates" are mapped to special GitHub comments.
  * Native GitHub parent/child sub-issue relationships are reconciled in a post-import pass using GitHub's native Sub-issues API.
  * Attachments (PDFs, Word documents, images, videos) are uploaded directly to Google Drive subfolders per issue and embedded as formatted links inside issue bodies.
  * Issue timestamps such as creation, close, or update date are preserved.
  * Issue states (open or closed), types (task/bug/feature), priority (urgent/high/medium/low) & estimate are mapped appropriately.
  * Issue comments are mapped to GitHub comments.
    * Jira issue references in normal and relationship comments are replaced by references to the created GitHub issue ID.
    * User account hashes are dynamically resolved to real display names using Jira user API lookups.
* Configure color scheme for labeling on import (`labelcolourselector.py`).
* Import multiple files to help overcome Jira's export limit of 1000 items (export multiple files by using the Jira key column as a range).

---

## Caveats

* **User Mapping:** This project does not map Jira users directly to GitHub user accounts:
  * The GitHub user (based on the personal access token used) performing the import will appear as the issue creator. The original Jira issue reporter is noted in the issue description footer.
  * The GitHub user performing the import will also appear as the comment creator (as GitHub's REST API does not support impersonation). The original Jira commenter's name & email (if available) are noted clearly within the comment text.

---

## Assumptions and Prerequisites

* It is strongly recommended to test your issue migration first with a test project in a test organization on GitHub.
* Input to the import script is the XML export file (or files) of your Jira project. (Tested with Jira Cloud)
* Your target GitHub project should already exist with the issue tracker enabled.
* There should be **NO existing issues or pull requests** in the target GitHub repository; otherwise, issue ID mappings will not match.

---

## Getting Started

### Setup

1. **Install `uv` (Recommended) & Dependencies:**
   We recommend using [`uv`](https://github.com/astral-sh/uv) for fast, isolated script execution and real-time terminal output logging.

   * **Install `uv`:**
     * **Windows (PowerShell):**
       ```powershell
       powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
       ```
     * **macOS / Linux:**
       ```bash
       curl -LsSf https://astral.sh/uv/install.sh | sh
       ```
       
2. **Clone this repository:**
   ```bash
   git clone https://github.com/touhidenosis/J2G-Migration-Tool.git
   cd J2G-Migration-Tool
   ```
   
3. **Set Up Virtual Environment:**
   Run the following commands in your shell to set up Python 3.11, install dependencies using `uv`, and prepare user caching:

   * **Windows (PowerShell):**
     ```powershell
     uv venv --python 3.11
     .\.venv\Scripts\activate
     uv pip install -r requirements.txt
     $env:PYTHONIOENCODING="utf-8" (Prevents UnicodeEncodeErrors by ensuring issue titles, comments, and logs stream smoothly without crashing mid-migration)
     ```

   * **macOS / Linux:**
     ```bash
     uv venv --python 3.11
     source .venv/bin/activate
     uv pip install -r requirements.txt
     export PYTHONIOENCODING="utf-8" (Prevents UnicodeEncodeErrors by ensuring issue titles, comments, and logs stream smoothly without crashing mid-migration)
     ```

4. **Configure Environment Variables (`.env`):**  
   Create a `.env` file in the root directory to store your credentials and avoid re-typing them on startup (check the .env.example file in the root directory of the code repo)

5. **Label Colors:**  
   Edit `labelcolourselector.py` if you want to customize how colors are assigned to created labels.

6. **Map Priorities & Issue Types in `config.py`:** 
Before running the migration, verify and adjust the mapping dictionaries in `config.py` to match your Jira priorities (e.g., Highest, High, Medium, Low, Lowest) and issue types (e.g., Bug, Task, Story) to your target GitHub setup.

7. **GitHub Personal Access Token:**  
   [Create a Personal Access Token in GitHub](https://docs.github.com/en/github/authenticating-to-github/creating-a-personal-access-token) with full `repo` permissions. Treat your tokens like passwords and keep them secret.

8. **Jira API Token:**  
   [Create an Atlassian API Token](https://id.atlassian.com/manage-profile/security/api-tokens) for your account. This is required for the script to query the Jira API to resolve user hashes to real display names (`jira_user_cache.json`).

9. **Google Drive Integration (For Attachments):**  
   If your Jira issues contain attachments (PDFs, images, docs):
   * Go to the [Google Cloud Console](https://console.cloud.google.com/) and create a new project.
   * Enable the **Google Drive API**.
   * Create **OAuth 2.0 Credentials** (Desktop app) or a **Service Account**, download the JSON key file, rename it to `credentials.json`, and place it in the root folder of this project.
   * Create a Google Drive folder to store attachments and copy its Folder ID into `GOOGLE_DRIVE_FOLDER_ID` in your `.env` file.
---

### Running the Tool

1. Export the desired Jira issues of your project ([see section below](#export-jira-issues)).

2. Execute the import script:
   ```bash
   uv run python -u main.py
   ```
   *Or using unbuffered output logging:*
   ```bash
   uv run python -u main.py | tee console_output.txt
   ```

3. **Prompts:** On startup, if environment variables are missing from `.env`, the script will prompt for:
   * The Jira XML export file name (semi-colon separated for multiple XML paths).
   * The Jira project name.
   * The `<statusCategoryId>` element's `id` attribute that signifies an issue as Done (integer, default `3`).
   * The GitHub account name that owns the repository (user or organization).
   * The target GitHub repository name.
   * The GitHub personal access token for authentication.
   * The index at which to start. Enter `0` to begin from scratch. If an execution was interrupted, enter the index number where the import stopped (refer to `jira-keys-to-github-id.txt`). Entering a number higher than `0` skips label re-importing and matches milestones to existing ones.

4. **Execution Pipeline:** The import process will:
   * Pre-scan Jira Cloud users via the API to resolve user hashes to real names (`jira_user_cache.json`).
   * Read the Jira XML export file and create an in-memory project representation of its contents.
   * Import milestones using the [GitHub Milestone API](https://docs.github.com/en/rest/issues/milestones).
   * Import labels using the [GitHub Label API](https://docs.github.com/en/rest/issues/labels).
   * Import issues and comments using the [GitHub Issue Import API](https://gist.github.com/jonmagic/5282384165e0f86ef105).
     * Attachments are streamed directly to Google Drive subfolders and embedded as clean links in issue bodies.
     * References to issues in comments are replaced with tracking placeholders during this step.
     * The GitHub Import API is asynchronous and avoids triggering secondary rate limits compared to standard issue creation endpoints.
   * Post-process native custom fields (Priority, Estimate, and Issue Type).
   * Run a secondary reconciliation pass to natively link child subtasks to parent tasks using GitHub's Sub-issues API.

---

### Export Jira Issues

1. Navigate to the Issue Search page in Jira Cloud: **Filters** -> **Search Issues** .
2. Select the project you want to export.
3. Specify your JQL query criteria and sort as needed. If you have more than 1000 items for example, use key ranges to export multiple XML files, e.g.:
   ```jql
   project = HOM AND issuekey <= HOM-1000 AND issuekey >= HOM-1 ORDER BY issuekey ASC
   ```
4. From the search results page, click the **Export** icon at the top right of the page.
5. Select **Export XML** and save the file into your migration folder.
