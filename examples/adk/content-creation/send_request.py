#!/usr/bin/env python3
"""Script to send a single request to the multi-agent system.

Usage:
    python send_request.py "Create a LinkedIn post about getting started with the Agent2Agent protocol"
    python send_request.py --message "Create a LinkedIn post about Python"
    python send_request.py --coordinator http://localhost:8093
"""

import argparse
import json
import sys
import uuid
from typing import Optional

import httpx


def send_request(
    message: str,
    coordinator_url: str = "http://localhost:8093",
    timeout: float = 300.0,
) -> dict:
    """Send a request to the coordinator.

    Args:
        message: Message content to send
        coordinator_url: Coordinator URL
        timeout: Request timeout in seconds

    Returns:
        Response data dictionary
    """
    # Build JSON-RPC request
    payload = {
        "id": str(uuid.uuid4()),
        "jsonrpc": "2.0",
        "method": "message/send",
        "params": {
            "message": {
                "kind": "message",
                "messageId": str(uuid.uuid4()),
                "parts": [
                    {
                        "kind": "text",
                        "text": message
                    }
                ],
                "role": "user"
            }
        }
    }

    print(f"Sending request to: {coordinator_url}")
    print(f"Message: {message}")
    print("-" * 60)

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                f"{coordinator_url}/",
                json=payload,
                headers={"Content-Type": "application/json"}
            )

            print(f"HTTP status code: {response.status_code}")

            if response.status_code != 200:
                print(f"Error response: {response.text}")
                return {"error": f"HTTP {response.status_code}", "detail": response.text}

            data = response.json()

            # Check JSON-RPC response
            if "error" in data:
                print(f"JSON-RPC error: {data['error']}")
                return data

            if "result" in data:
                print("✓ Request successful")

                # Try to extract response content
                result = data.get("result", {})

                # Check for message
                message_data = result.get("message", {})
                if message_data:
                    parts = message_data.get("parts", [])
                    for part in parts:
                        if part.get("kind") == "text":
                            # Show full text without truncation
                            print(f"\nResponse message: {part.get('text', '')}")

                # Check for artifacts
                artifacts = result.get("artifacts", [])
                if artifacts:
                    print(f"\nResponse contains {len(artifacts)} artifact(s):")
                    for i, artifact in enumerate(artifacts, 1):
                        artifact_name = artifact.get("name", "unnamed")
                        artifact_parts = artifact.get("parts", [])
                        print(f"  Artifact {i} (name: {artifact_name}):")
                        for part in artifact_parts:
                            if part.get("kind") == "text":
                                text = part.get("text", "")
                                # Show full text without truncation
                                if text:
                                    print(f"    {text}")
                                else:
                                    print(f"    (empty)")

                # Also check history for any agent messages with content
                history = result.get("history", [])
                if history:
                    print(f"\nConversation history ({len(history)} messages):")
                    for i, msg in enumerate(history, 1):
                        role = msg.get("role", "unknown")
                        parts = msg.get("parts", [])
                        for part in parts:
                            if part.get("kind") == "text":
                                text = part.get("text", "")
                                if text and len(text) > 20:  # Only show substantial messages
                                    # Show full text without truncation
                                    print(f"  [{i}] {role}: {text}")

                return data
            else:
                print(f"Unknown response format: {data}")
                return data

    except httpx.TimeoutException:
        print(f"✗ Request timeout (exceeded {timeout} seconds)")
        return {"error": "timeout"}
    except httpx.RequestError as e:
        print(f"✗ Request failed: {e}")
        return {"error": str(e)}
    except Exception as e:
        print(f"✗ Error occurred: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(
        description="Send a single request to the multi-agent system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Use default message
  python send_request.py

  # Custom message
  python send_request.py "Create a blog post about Python"

  # Specify coordinator URL
  python send_request.py --coordinator http://localhost:8093 --message "Your message"

  # Display full JSON response
  python send_request.py --message "Your message" --json
        """
    )

    parser.add_argument(
        "message",
        nargs="?",
        default="Create a LinkedIn post about getting started with the Agent2Agent protocol",
        help="Message to send (default: LinkedIn post request)."
    )

    parser.add_argument(
        "--coordinator",
        default="http://localhost:8093",
        help="Coordinator URL (default: http://localhost:8093)"
    )

    parser.add_argument(
        "--message",
        dest="message_opt",
        help="Specify message via --message argument (overrides positional argument)"
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Request timeout in seconds (default: 300)"
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Output full response in JSON format"
    )

    args = parser.parse_args()

    # Prefer --message argument
    message = args.message_opt if args.message_opt else args.message

    # Send request
    result = send_request(
        message=message,
        coordinator_url=args.coordinator,
        timeout=args.timeout
    )

    # If JSON output is needed
    if args.json:
        print("\n" + "=" * 60)
        print("Full JSON Response:")
        print("=" * 60)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    # Return appropriate exit code
    if "error" in result:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
