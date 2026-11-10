import os
from collections import defaultdict
from html.entities import name2codepoint
from dateutil.parser import parse
from datetime import datetime
import re

from utils import fetch_labels_mapping, fetch_allowed_labels, convert_label


class Project:

    def __init__(self, name, doneStatusCategoryId, jiraBaseUrl):
        self.name = name
        self.doneStatusCategoryId = doneStatusCategoryId
        self.jiraBaseUrl = jiraBaseUrl
        self._project = {'Milestones': defaultdict(int), 'Components': defaultdict(
            int), 'Labels': defaultdict(int), 'Types': defaultdict(int), 'Issues': []}

        self.labels_mapping = fetch_labels_mapping()
        self.approved_labels = fetch_allowed_labels()

        # Keep Jira parent/child relationships separate from the GitHub issue payload
        self._parent_children = defaultdict(set)
        

    def get_milestones(self):
        return self._project['Milestones']

    def get_components(self):
        return self._project['Components']

    def get_issues(self):
        return self._project['Issues']

    def get_types(self):
        return self._project['Types']

    def get_all_labels(self):
        merge = self._project['Components'].copy()
        merge.update(self._project['Labels'])
        merge.update(self._project['Types'])
        merge.update({'imported-jira-issue': 0})
        return merge

    def get_labels(self):
        merge = self._project['Labels'].copy()
        merge.update({'imported-jira-issue': 0})
        return merge

    def get_parent_children(self):
        return self._parent_children

    def add_item(self, item):
        itemProject = self._projectFor(item)
        if itemProject != self.name:
            print('Skipping item ' + item.key.text + ' for project ' +
                  itemProject + ' current project: ' + self.name)
            return

        self._append_item_to_project(item)

        self._add_milestone(item)

        self._add_labels(item)

        # self._add_subtasks(item)

        self._add_parenttask(item)

        self._add_comments(item)

        self._add_relationships(item)

    def prettify(self):
        def hist(h):
            for key in h.keys():
                print(('%30s (%5d): ' + h[key] * '#') % (key, h[key]))
            print

        print(self.name + ':\n  Milestones:')
        hist(self._project['Milestones'])
        print('  Types:')
        hist(self._project['Types'])
        print('  Components:')
        hist(self._project['Components'])
        print('  Labels:')
        hist(self._project['Labels'])
        print
        print('Total Issues to Import: %d' % len(self._project['Issues']))
        print('\n>>> READY: Press [ENTER] to begin the migration or Press [CTRL+C] to Cancel... \n')


    def _projectFor(self, item):
        try:
            result = item.project.get('key')
        except AttributeError:
            result = item.key.text.split('-')[0]
        return result

    def _append_item_to_project(self, item):
        # todo assignee
        closed = str(item.statusCategory.get('id')) == self.doneStatusCategoryId
        closed_at = ''
        if closed:
            try:
                closed_at = self._convert_to_iso(item.resolved.text)
            except AttributeError:
                pass

        # TODO: ensure item.assignee/reporter.get('username') to avoid "JENKINSUSER12345"
        # TODO: fixit in gh issues
        # check if issue description is missing or empty and set a default
        if not hasattr(item, 'description') or not item.description:
            item.description = 'No Description'
        body = self._htmlentitydecode(item.description.text)

        # --- METADATA: ORIGINAL AUTHOR & SUMMARY ---
        try:
            reporter_name = item.reporter.text or 'unknown'
            reporter_account_id = item.reporter.get('accountid')
            
            # Load phonebook file
            phonebook = {}
            try:
                import json
                import os
                cache_path = os.path.join(os.path.dirname(__file__), 'jira_user_cache.json')
                if os.path.exists(cache_path):
                    with open(cache_path, 'r', encoding='utf-8') as f:
                        phonebook = json.load(f)
            except Exception:
                pass
            
            reporter_display = reporter_name
            if reporter_account_id and reporter_account_id in phonebook:
                user_info = phonebook.get(reporter_account_id)
                if user_info and isinstance(user_info, str):
                    # SANITIZE BRACKETS: Replace angle brackets with square brackets to prevent HTML hiding
                    reporter_display = user_info.replace('<', '').replace('>', '').strip()
            
            # Render metadata text block
            body += '\n\n---\n<details><summary><i>Originally reported by <b>' + reporter_display + '</b>, imported from: <a href="' + self.jiraBaseUrl + '/browse/' + item.key.text + '" target="_blank" rel="noopener noreferrer">' + item.title.text[item.title.text.index("]") + 2:] + '</a></i></summary>'

        except AttributeError:
            pass
        
        # --- METADATA: ASSIGNEE ---
        body = body + '\n<i><ul>'
        
        try:
            if item.assignee is not None and item.assignee.text:
                assignee_name = item.assignee.text
                assignee_account_id = item.assignee.get('accountid')
                
                if assignee_name and assignee_name.lower() != 'unassigned':
                    assignee_display = assignee_name
                    if assignee_account_id and assignee_account_id in phonebook:
                        user_info = phonebook.get(assignee_account_id)
                        if user_info and isinstance(user_info, str):
                            # SANITIZE BRACKETS: Replace angle brackets with square brackets to prevent HTML hiding
                            assignee_display = user_info.replace('<', '').replace('>', '').strip()
                    
                    body += '\n<li><b>Assignee</b>: ' + assignee_display
        except AttributeError:
            pass
        
        try:
            body = body + '\n<li><b>Status</b>: ' + item.status
        except AttributeError:
            pass
        
        # Added
        try:
            if item.timeoriginalestimate:
                body += "\n<li><b>Original estimate</b>: " + item.timeoriginalestimate.text
        except AttributeError:
            pass
        
        # Added
        try:
            fields_to_display = {
                "Start date": "Start date",
                "Sprint": "Sprint"
            }

            for cf in item.customfields.customfield:
                field_name = cf.customfieldname.text
                
                if field_name in fields_to_display:
                    body += "\n<li><b>" + fields_to_display[field_name] + "</b>: " + cf.customfieldvalues.customfieldvalue.text

        except AttributeError:
            pass
        
        # Added
        try:
            if item.due:
                body += "\n<li><b>Due date</b>: " + item.due.text
        except AttributeError:
            pass

        try:
            body = body + '\n<li><b>Priority</b>: ' + item.priority
        except AttributeError:
            pass
        try:
            body = body + '\n<li><b>Resolution</b>: ' + item.resolution
        except AttributeError:
            pass
        try:
            body = body + '\n<li><b>Resolved</b>: ' + self._convert_to_iso(item.resolved.text)
        except AttributeError:
            pass
        body = body + '\n<li><b>Imported</b>: ' + datetime.today().strftime('%Y-%m-%d')
        body = body + '\n</ul></i>\n</details>'

        # retrieve jira components and labels as github labels
        labels = []

        # REMOVED/COMMENTED OUT the default fallback logic so empty components stay empty:
        # if not hasattr(item, 'component') or not item.component:
        #     item.component = 'miscellaneous'
        
        # Check if the item actually has components before looping
        if hasattr(item, 'component') and item.component:
            for component in item.component:
                if os.getenv('JIRA_MIGRATION_INCLUDE_COMPONENT_IN_LABELS', 'true') == 'true':
                    if component.text:
                        labels.append('component:' + component.text.lower())
                        print(f"Debug: component.text.lower = {component.text.lower()}")

        if not hasattr(item, 'labels') or not item.labels:
            pass
        else:
            mapped_type = self._jira_type_mapping(item.type.text.lower())
            if mapped_type:
                labels.append(mapped_type)
        
        for label in item.labels.findall('label'):
            converted_label = convert_label(label.text.strip().lower(), self.labels_mapping, self.approved_labels)
            if converted_label is not None:
                labels.append(converted_label)

        labels.append('imported-jira-issue')

        unique_labels = list(set(labels))

        # --- NEW CODE:Track priority value string safely out of XML object structure 
        jira_priority = None
        try:
            if hasattr(item, 'priority') and item.priority.text:
                jira_priority = item.priority.text.strip()
        except AttributeError:
            pass
        
        # --- NEW CODE:Extract issue type value safely out of XML object structure 
        jira_type = None
        try:
            if hasattr(item, 'type') and item.type.text:
                jira_type = item.type.text.strip()
        except AttributeError:
            pass
        
        # Extract the original Jira estimate value safely
        jira_estimate = None
        try:
            if hasattr(item, 'timeoriginalestimate') and item.timeoriginalestimate.text:
                jira_estimate = item.timeoriginalestimate.text.strip()
        except AttributeError:
            pass
        
        # --- FIXED: TRUE-FILENAME PRESERVING ATTACHMENT PARSER ---
        attachments = []
        try:
            # Step 1: Build a map of Attachment ID -> True Filename from native XML nodes
            id_to_true_name = {}
            for attach_node in item.findall('.//attachment'):
                node_id = attach_node.get('id')
                node_name = attach_node.get('name')
                attach_url = attach_node.get('value') or attach_node.text
                
                if node_id and node_name:
                    id_to_true_name[node_id.strip()] = node_name.strip()
                
                if attach_url:
                    final_native_url = attach_url.strip() if attach_url.strip().startswith('http') else f"https://enosisbd.atlassian.net{attach_url.strip()}"
                    attachments.append({
                        'name': node_name.strip() if node_name else f"attachment_{node_id}",
                        'url': final_native_url
                    })
            
            # Step 2: Extract embedded string assets from comments and body text safely
            import re
            search_pool = body
            if hasattr(item, 'comments') and item.comments is not None:
                for c in item.findall('.//comment'):
                    if c.text:
                        search_pool += " " + c.text

            # Find the complete HTML tag block enclosing the attachment link
            tag_pattern = re.compile(r'(<(?:a|object|img)\s+[^>]*?(?:href|data|src)="([^"]*/rest/api/3/attachment/content/(\d+)[^"]*)"[^>]*?>)', re.IGNORECASE)
            
            for full_tag, url, id_num in tag_pattern.findall(search_pool):
                clean_url = url.replace('&amp;', '&').strip()
                id_str = id_num.strip()
                
                # TRUE LOOKUP: Prioritize matching the exact filename from the native node first
                if id_str in id_to_true_name:
                    name = id_to_true_name[id_str]
                else:
                    # Fallback context loop if the asset only exists as an inline tag
                    full_tag_lower = full_tag.lower()
                    if "type=\"video/mp4\"" in full_tag_lower or "video" in full_tag_lower or ".mp4" in clean_url:
                        name = f"video_{id_str}.mp4"
                    elif "<img" in full_tag_lower or "image" in full_tag_lower or ".png" in clean_url:
                        name = f"image_{id_str}.png"
                    else:
                        name = f"embedded_asset_{id_str}"
                    
                    # Try to pull alternative text names from attributes
                    alt_match = re.search(r'alt="([^"]*?' + id_str + r'[^"]*?)"', full_tag, re.IGNORECASE)
                    if alt_match:
                        name = alt_match.group(1)
                    else:
                        title_match = re.search(r'title="([^"]*?)"', full_tag, re.IGNORECASE)
                        if title_match and not title_match.group(1).lower() in ['smart-link', 'external-link']:
                            name = title_match.group(1)

                final_embed_url = clean_url if clean_url.startswith('http') else f"https://enosisbd.atlassian.net{clean_url}"

                # Prevent adding duplicate asset link objects to the payload array
                if not any(a['url'] == final_embed_url or final_embed_url in a['url'] for a in attachments):
                    attachments.append({
                        'name': name.strip(),
                        'url': final_embed_url
                    })
        except Exception as e:
            print(f"Warning: Failed parsing XML attachment nodes safely: {e}")

        self._project['Issues'].append({'title': item.title.text,
                                        'key': item.key.text,
                                        'body': body,
                                        'created_at': self._convert_to_iso(item.created.text),
                                        'closed_at': closed_at,
                                        'updated_at': self._convert_to_iso(item.updated.text),
                                        'closed': closed,
                                        'labels': unique_labels,
                                        'comments': [],
                                        'duplicates': [],
                                        'is-duplicated-by': [],
                                        'is-related-to': [],
                                        'depends-on': [],
                                        'blocks': [],
                                        'priority_field': jira_priority, # Add tracking field here
                                        'type_field': jira_type, # Add tracking type field here
                                        'estimate_field': jira_estimate, # Add tracking field here
                                        'attachments': attachments # Add tracking field here
                                        })
        if not self._project['Issues'][-1]['closed_at']:
            del self._project['Issues'][-1]['closed_at']

    def _jira_type_mapping(self, issue_type):
        if issue_type == 'bug':
            return 'bug'
        if issue_type == 'improvement':
            return 'task'
        if issue_type == 'new feature':
            return 'task'
        if issue_type == 'task':
            return 'task'
        if issue_type == 'story':
            return 'story'
        if issue_type == 'patch':
            return 'task'
        if issue_type == 'epic':
            return 'epic'

    def _convert_to_iso(self, timestamp):
        dt = parse(timestamp)
        return dt.isoformat()

    def _add_milestone(self, item):
        try:
            self._project['Milestones'][item.fixVersion.text] += 1
            # this prop will be deleted later:
            self._project['Issues'][-1]['milestone_name'] = item.fixVersion.text.trim()
        except AttributeError:
            pass

    def _add_labels(self, item):
        try:
            # Safely handle multiple components as an iterable loop
            if hasattr(item, 'component') and item.component:
                # If it's a single node element, wrap it in a list to make it iterable
                components = item.component if isinstance(item.component, list) else [item.component]
                
                for component in components:
                    if component.text:
                        clean_comp = component.text.strip()
                        # Track the count for the prettify() histogram report metrics
                        self._project['Components'][clean_comp] += 1
        except AttributeError:
            pass
        
        try:
            for label in item.labels.label:
                self._project['Labels'][label.text] += 1
                tmp_l = label.text.trim()
                if tmp_l == 'Bug':
                    tmp_l = 'bug'

                self._project['Issues'][-1]['labels'].append(tmp_l)
        except AttributeError:
            pass

        try:
            self._project['Types'][item.type.text] += 1
            tmp_l = item.type.text.trim()
            if tmp_l == 'Bug':
                tmp_l = 'bug'

            self._project['Issues'][-1]['labels'].append(tmp_l)
        except AttributeError:
            pass

    # def _add_subtasks(self, item):
    #     try:
    #         subtaskList = ''
    #         for subtask in item.subtasks.subtask:
    #             subtaskList = subtaskList + '- ' + subtask + '\n'
    #         if subtaskList != '':
    #             print('-> subtaskList: ' + subtaskList)
    #             self._project['Issues'][-1]['comments'].append(
    #                 {"created_at": self._convert_to_iso(item.created.text),
    #                  "body": 'Subtasks:\n\n' + subtaskList})
    #     except AttributeError:
    #         pass
        
    def _add_subtasks(self, item):
        try:
            subtaskList = ''
            
            for subtask in item.subtasks.subtask:
                subtask_link = f"[{subtask}]({self.jiraBaseUrl}/browse/{subtask})"
                subtaskList += f"- {subtask_link}\n"
                
            if subtaskList != '':
                print('-> subtaskList: ' + subtaskList)
                self._project['Issues'][-1]['comments'].append(
                    {"created_at": self._convert_to_iso(item.created.text),
                     "body": 'Subtasks:\n\n' + subtaskList})
        except AttributeError:
            pass

    # def _add_parenttask(self, item):
    #     try:
    #         parentTask = item.parent.text
    #         if parentTask != '':
    #             print('-> parentTask: ' + parentTask)
    #             self._project['Issues'][-1]['comments'].append(
    #                 {"created_at": self._convert_to_iso(item.created.text),
    #                  "body": 'Subtask of parent task ' + parentTask})
    #     except AttributeError:
    #         pass

    # def _add_parenttask(self, item):
    #     try:
    #         parentTask = item.parent.text  # This holds the Key (e.g., 'HOM-150')
    #         parentTitle = item.parent.get('title') # This safely extracts the title attribute if present
            
    #         if parentTask and parentTask.strip() != '':
    #             print('-> parentTask: ' + parentTask)
                
    #             # Create a direct Markdown link to the original Jira ticket
    #             jira_link = f"[{parentTask}]({self.jiraBaseUrl}/browse/{parentTask})"
                
    #             # Check if we successfully grabbed a descriptive title string
    #             if parentTitle and parentTitle.strip() != '':
    #                 body_text = f"Subtask of parent Jira task: {jira_link} - {parentTitle.strip()}"
    #             else:
    #                 body_text = f"Subtask of parent Jira task: {jira_link}"

    #             self._project['Issues'][-1]['comments'].append(
    #                 {
    #                     "created_at": self._convert_to_iso(item.created.text),
    #                     "body": body_text
    #                 }
    #             )
    #     except AttributeError:
    #         pass

    def _add_parenttask(self, item):
        try:
            parentTask = item.parent.text  # This holds the Key (e.g., 'HOM-150')
            parentTitle = item.parent.get('title') or '' # Safely extract title or default to empty string
            
            if parentTask and parentTask.strip() != '':

                parent_key = parentTask.strip()
                child_key = item.key.text.strip()
                self._parent_children[parent_key].add(child_key)  # <-- ADD THIS LINE
                
                # Extract the current Jira issue key (e.g., 'HOM-64')
                # mainTask = item.key.text
                
                # Formatted console output showing the relationship direction
                # print(f'\n-> Task: {mainTask} => parentTask: {parentTask}')
                
                # TRACKING TOKEN: Embed both the Key and Title separated by our custom delimiter
                body_text = f"TRACK_PARENT_KEY_PLACEHOLDER:{parentTask.strip()}###SPLIT###{parentTitle.strip()}"

                self._project['Issues'][-1]['comments'].append(
                    {
                        "created_at": self._convert_to_iso(item.created.text),
                        "body": body_text
                    }
                )
        except AttributeError:
            pass

    # def _add_comments(self, item):
    #     try:
    #         for comment in item.comments.comment:
    #             self._project['Issues'][-1]['comments'].append(
    #                 {"created_at": self._convert_to_iso(comment.get('created')),
    #                  "body": '<i><a href="' + self.jiraBaseUrl + '/secure/ViewProfile.jspa?name=' + comment.get('author') + '">' + comment.get('author') + '</a>:</i>\n' + self._htmlentitydecode(comment.text)
    #                  })
    #     except AttributeError:
    #         pass

    # def _add_comments(self, item):
    #     try:
    #         for comment in item.comments.comment:
    #             author = comment.get('author') or 'unknown'
    #             account_id = comment.get('author')
    #             author_name = author

    #             # Try to extract display name from Jira user-hover link inside comment HTML
    #             comment_html = self._htmlentitydecode(comment.text)

    #             if 'class="user-hover"' in comment_html or 'class="user-hover"' in comment_html.replace("'", '"'):
    #                 try:
    #                     author_name = comment_html.split('>')[1].split('<')[0]
    #                 except (IndexError, AttributeError):
    #                     pass

    #             self._project['Issues'][-1]['comments'].append(
    #                 {
    #                     "created_at": self._convert_to_iso(comment.get('created')),
    #                     "body": '<i><a href="' + self.jiraBaseUrl + '/secure/ViewProfile.jspa?accountId=' + account_id + '" target="_blank" rel="noopener noreferrer">' + author_name + '</a>:</i>\n' + comment_html
    #                 }
    #             )

    #     except AttributeError:
    #         pass

    # def _add_comments(self, item):
    #     try:
    #         for comment in item.comments.comment:
    #             account_id = comment.get('author') or 'unknown'
    #             author_name = account_id

    #             comment_html = self._htmlentitydecode(comment.text)

    #             # Extract Jira user display name from user-hover link
    #             match = re.search(r'class="user-hover"[^>]*>(.*?)</a>', comment_html)

    #             if match:
    #                 author_name = match.group(1)

    #             self._project['Issues'][-1]['comments'].append(
    #                 {
    #                     "created_at": self._convert_to_iso(comment.get('created')),
    #                     "body": '<i><a href="' + self.jiraBaseUrl +
    #                             '/secure/ViewProfile.jspa?accountId=' + account_id +
    #                             '" target="_blank" rel="noopener noreferrer">' +
    #                             author_name +
    #                             '</a>:</i>\n' + comment_html
    #                 }
    #             )

    #     except AttributeError:
    #         pass

    def _add_comments(self, item):
        try:
            for comment in item.comments.comment:
                account_id = comment.get('author') or 'unknown'
                comment_html = self._htmlentitydecode(comment.text)

                # Pass the raw account_id cleanly in the link text so importer.py can handle lookups perfectly
                self._project['Issues'][-1]['comments'].append(
                    {
                        "created_at": self._convert_to_iso(comment.get('created')),
                        "body": '<i><a href="' + self.jiraBaseUrl +
                                '/secure/ViewProfile.jspa?accountId=' + account_id +
                                '" target="_blank" rel="noopener noreferrer">' +
                                account_id +
                                '</a>:</i>\n' + comment_html
                    }
                )
        except AttributeError:
            pass

    def _add_relationships(self, item):
        try:
            for issuelinktype in item.issuelinks.issuelinktype:
                for outwardlink in issuelinktype.outwardlinks:
                    for issuelink in outwardlink.issuelink:
                        for issuekey in issuelink.issuekey:
                            tmp_outward = outwardlink.get("description").replace(' ', '-')
                            if tmp_outward in self._project['Issues'][-1]:
                                self._project['Issues'][-1][tmp_outward].append(issuekey.text)
        except AttributeError:
            pass
        except KeyError:
            print('1. KeyError at ' + item.key.text)
        try:
            for issuelinktype in item.issuelinks.issuelinktype:
                for inwardlink in issuelinktype.inwardlinks:
                    for issuelink in inwardlink.issuelink:
                        for issuekey in issuelink.issuekey:
                            tmp_inward = inwardlink.get("description").replace(' ', '-')
                            if tmp_inward in self._project['Issues'][-1]:
                                self._project['Issues'][-1][tmp_inward].append(issuekey.text)
        except AttributeError:
            pass
        except KeyError:
            print('2. KeyError at ' + item.key.text)

        for customfield in item.customfields.findall('customfield'):
            if customfield.get('key') == 'com.pyxis.greenhopper.jira:gh-epic-link':
                epic_key = customfield.customfieldvalues.customfieldvalue
                self._project['Issues'][-1]['epic-link'] = epic_key

    def _htmlentitydecode(self, s):
        if s is None:
            return ''
        s = s.replace(' ' * 8, '')
        return re.sub('&(%s);' % '|'.join(name2codepoint),
                      lambda m: chr(name2codepoint[m.group(1)]), s)
