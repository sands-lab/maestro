import random
import uuid
from locust import HttpUser, task, between

# 1. 确保 queries.py 在同一个文件夹中
try:
    from queries import SAMPLE_QUERIES
except ImportError:
    raise ImportError("CRITICAL: 'queries.py' file not found. Please create it.")

class AgentUser(HttpUser):
    """
    模拟一个通过 JSON-RPC 协议与 Host Agent 交互的真实用户。
    (使用 'message/send' 方法)
    """
    
    # 用户在发送下一个请求前会等待1到3秒，模拟“思考时间”
    wait_time = between(1, 3)

    def on_start(self):
        """
        当一个新用户开始时。
        (注意：这个新 payload 结构中没有 context_id，
         所以我们暂时不使用它，但保留 on_start 以备将来使用)
        """
        # self.context_id = str(uuid.uuid4().hex) # 暂时禁用
        print(f"--- New user started ---")

    @task
    def send_agent_query(self):
        """
        主要的测试任务：构建并发送一个 "message/send" 请求
        """
        
        # 1. 从导入的列表中随机选择一个查询
        query_text = random.choice(SAMPLE_QUERIES)

        # 2. 🔴 构建新的 JSON-RPC Payload
        # 这个结构是基于您新抓取的 "message/send" 请求
        final_payload = {
            "id": str(uuid.uuid4()),
            "jsonrpc": "2.0",
            "method": "message/send",  # <-- 使用您发现的正确方法
            "params": {
                "message": {
                    "kind": "message",
                    "messageId": str(uuid.uuid4()),
                    "parts": [
                        {
                            "kind": "text",
                            "text": query_text
                        }
                    ],
                    "role": "user"
                }
            }
        }

        # 3. 发送 HTTP POST 请求
        # 使用我们之前用 curl 验证过的、正确的路径
        api_endpoint_path = "/api/a2a/kagent/coordinator-agent/" 

        with self.client.post(
            api_endpoint_path,
            json=final_payload,
            name=api_endpoint_path,
            catch_response=True 
        ) as response:
            
            # 4. 检查响应
            if response.status_code == 200:
                try:
                    data = response.json()
                    if "result" in data:
                        response.success()
                    elif "error" in data:
                        response.failure(f"JSON-RPC Error: {data['error']}")
                    else:
                        response.failure("Invalid JSON-RPC response (missing result/error)")
                except Exception as e:
                    response.failure(f"Failed to parse JSON: {str(e)}")
            else:
                # 报告任何 HTTP 错误 (例如 404, 500, 503)
                response.failure(f"Status code was {response.status_code}")