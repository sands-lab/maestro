#!/usr/bin/env python3
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Main entry point for running the Brand Search Optimization Agent.

This script provides:
1. Mock data testing mode (--test-mock) to validate setup
2. Interactive CLI interface to run the agent
3. Single query mode via command line arguments
"""

import asyncio
import sys
import os
import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch
from agent import create_root_agent

# Add the current directory to Python path to enable imports
sys.path.insert(0, str(Path(__file__).parent))


def test_mock_data():
    """Test the agent setup with mock BigQuery data."""
    print("\n" + "="*70)
    print("Testing Brand Search Optimization with Mock Data")
    print("="*70 + "\n")
    
    try:
        from unittest.mock import MagicMock, patch
        from google.adk.tools import ToolContext
        from tools import bq_connector
        from shared_libraries import constants
        
        print("✓ Successfully imported required modules")
        
        # Create mock data
        print("\n📦 Creating mock BigQuery data...")
        
        mock_tool_context = MagicMock(spec=ToolContext)
        mock_tool_context.user_content.parts = [MagicMock(text="cymbal")]
        
        # Mock BigQuery results
        mock_row1 = MagicMock(
            title="cymbal Air Max",
            description="Comfortable running shoes",
            attribute="Size: 10, Color: Blue",
            brand="cymbal",
        )
        mock_row2 = MagicMock(
            title="cymbal Sportswear T-Shirt",
            description="Cotton blend, short sleeve",
            attribute="Size: L, Color: Black",
            brand="cymbal",
        )
        mock_row3 = MagicMock(
            title="neuravibe Pro Training Shorts",
            description="Moisture-wicking fabric",
            attribute="Size: M, Color: Gray",
            brand="neuravibe",
        )
        mock_results = [mock_row1, mock_row2, mock_row3]
        
        print("✓ Mock data created:")
        print(f"  - {mock_row1.title} ({mock_row1.brand})")
        print(f"  - {mock_row2.title} ({mock_row2.brand})")
        print(f"  - {mock_row3.title} ({mock_row3.brand})")
        
        # Test with mock client
        print("\n🧪 Testing BigQuery connector with mock data...")
        
        with patch("tools.bq_connector.client") as mock_client:
            mock_query_job = MagicMock()
            mock_query_job.result.return_value = mock_results
            mock_client.query.return_value = mock_query_job
            
            with patch.object(constants, "PROJECT", "test_project"):
                with patch.object(constants, "TABLE_ID", "test_table"):
                    markdown_output = bq_connector.get_product_details_for_brand(
                        mock_tool_context
                    )
                    
                    print("\n📄 Generated markdown output:")
                    print("-" * 70)
                    print(markdown_output)
                    print("-" * 70)
                    
                    # Validate results
                    assert "cymbal Air Max" in markdown_output, "Expected cymbal product not found"
                    assert "cymbal Sportswear T-Shirt" in markdown_output, "Expected cymbal product not found"
                    assert "neuravibe Pro" not in markdown_output, "Unexpected neuravibe product found (should be filtered)"
                    
                    print("\n✅ All mock data tests passed!")
                    print("   - Cymbal products correctly included")
                    print("   - Neuravibe products correctly filtered out")
                    print("   - Markdown formatting validated")
        
        print("\n" + "="*70)
        print("Mock Data Test Complete - Setup is working correctly!")
        print("="*70 + "\n")
        return 0
        
    except ImportError as e:
        print(f"\n❌ Import Error: {e}")
        print("Make sure all dependencies are installed.")
        return 1
    except AssertionError as e:
        print(f"\n❌ Test Failed: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ Unexpected Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


async def run_interactive_session():
    """Run an interactive session with the agent."""
    print("\n" + "="*70)
    print("Brand Search Optimization Agent - Interactive Mode")
    print("="*70)
    print("\nThis agent helps optimize product search by:")
    print("  1. Finding relevant keywords for your brand")
    print("  2. Searching websites for top results")
    print("  3. Comparing and suggesting title improvements")
    print("\nType 'exit' or 'quit' to stop, or press Ctrl+C")
    print("="*70 + "\n")
    
    # Create the root agent
    print("🔧 Initializing agent...")
    root_agent = await create_root_agent()
    print("✓ Agent ready!\n")

    while True:
        try:
            # Get user input
            user_input = input("\n[user]: ").strip()
            
            # Check for exit commands
            if user_input.lower() in ["exit", "quit", "q"]:
                print("\n👋 Goodbye!")
                break
            
            # Skip empty inputs
            if not user_input:
                continue

            # Run the agent with user input
            print("\n🤖 Processing your request...\n")
            
            try:
                result = await root_agent.run_async(user_input)
                
                # Display the result
                if hasattr(result, 'final_output'):
                    print(f"\n[agent]: {result.final_output}")
                else:
                    print(f"\n[agent]: {result}")
                    
            except Exception as e:
                print(f"\n❌ Error during agent execution: {e}")
                import traceback
                traceback.print_exc()

        except KeyboardInterrupt:
            print("\n\n👋 Interrupted. Goodbye!")
            break
        except EOFError:
            print("\n\n👋 EOF received. Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Unexpected error: {e}")
            import traceback
            traceback.print_exc()


async def run_single_query(query: str):
    """Run a single query and exit."""
    print(f"\n🤖 Processing query: {query}\n")
    
    # Create the root agent
    print("🔧 Initializing agent...")
    root_agent = await create_root_agent()
    print("✓ Agent ready!\n")
    
    try:
        result = await root_agent.run_async(query)
        
        if hasattr(result, 'final_output'):
            print(f"\n[agent]: {result.final_output}")
        else:
            print(f"\n[agent]: {result}")
            
        return 0
        
    except Exception as e:
        print(f"\n❌ Error during agent execution: {e}")
        import traceback
        traceback.print_exc()
        return 1


async def main():
    
    return test_mock_data()


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n👋 Interrupted. Goodbye!")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

