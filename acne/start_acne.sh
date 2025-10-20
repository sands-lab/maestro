GRAFANA_API_KEY="glsa_eqmCxSQFsTZfreH4pJtMGH02fGVKmEkE_9d35ddaf"
GRAFANA_URL="http://localhost:3000"
DASHBOARD_UID="pMEd7m0Mz"
EXPERIMENT_NAME="MAS Test 01"

START_TIME=$(date +%s%3N)

uv run . --agent http://127.0.0.1:8083/api/a2a/kagent/coordinator-agent

END_TIME=$(date +%s%3N)

# 调用 API，一次性提交一个带有开始和结束时间的 "区域注释"
curl -X POST "$GRAFANA_URL/api/annotations" \
  -H "Authorization: Bearer $GRAFANA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "time": '$START_TIME',
    "timeEnd": '$END_TIME',
    "isRegion": true,
    "text": "'"$EXPERIMENT_NAME"'",
    "tags": ["mas-test"]
  }'

FINAL_URL="$GRAFANA_URL/d/$DASHBOARD_UID?from=$START_TIME&to=$END_TIME"

echo "======================================================"
echo "Experiment complete!"
echo "View results at the link below:"
echo "$FINAL_URL"
echo "======================================================"