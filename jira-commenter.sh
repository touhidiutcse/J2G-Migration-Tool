#!/usr/bin/env bash
USER="<your-jenkins-username"
PWD="<your-jenkins-pwd>"
JIRA="https://issues.jenkins.io"
GITHUB="https://github.com/jenkins-infra/helpdesk/issues/"
FILE="jira-keys-to-github-id.txt" # with each line containing <JENKINS-ISSUE-KEY>:<GITHUB-ISSUE-KEY>, ex: "INFRA-545:415"

while IFS=':' read -ra ADDR; do
    echo "processing:: $GITHUB${ADDR[1]} >> ${JIRA}/browse/${ADDR[0]}"
    ID=${ADDR[1]}
    KEY=${ADDR[0]}
    BODY="For your information, [all INFRA issues|${JIRA}/projects/INFRA/issues/] related to the [Jenkins Infrastructure project|https://www.jenkins.io/projects/infrastructure/] have been transferred to Github: ${GITHUB}\n\nHere is the direct link to this issue in Github: ${GITHUB}/${ID}\nAnd here is the link to a search for related issues: ${GITHUB}?q=%22${KEY}%22\n\n(Note: this is an automated bulk comment)"
    # https://developer.atlassian.com/server/jira/platform/jira-rest-api-examples/#adding-a-comment
    curl \
    -D- \
    -u ${USER}:${PWD} \
    -X POST \
    --data "{\"body\": \"${BODY}\"}" \
    -H "Content-Type: application/json" \
    "${JIRA}/rest/api/2/issue/${KEY}/comment"

done <${FILE}
