"""
Search UI Tools

Provides tools for launching and managing the search UI.
"""

import os
import subprocess
import sys
from pathlib import Path


SEARCH_UI_HOST = os.getenv("SEARCH_UI_HOST", "127.0.0.1")
SEARCH_UI_PORT = int(os.getenv("SEARCH_UI_PORT", "8765"))


def launch_search_ui(index_name: str, port: int = None) -> str:
    """
    Launch the search UI for testing queries against an index.
    
    Note: This is a simplified version that provides instructions for manual launch.
    The full React UI server can be added later.
    
    Args:
        index_name: Index name to search against
        port: Port to run UI on (default from env or 8765)
    
    Returns:
        str: Instructions for accessing the UI
    """
    try:
        ui_port = port or SEARCH_UI_PORT
        
        # Check if UI static files exist
        server_dir = Path(__file__).parent.parent.parent
        ui_dir = server_dir / "ui" / "search_builder"
        
        if not ui_dir.exists():
            return (
                f"⚠️  Search UI static files not found at {ui_dir}\n\n"
                f"To test queries against index '{index_name}':\n\n"
                f"1. Use OpenSearch Dashboards Dev Tools:\n"
                f"   http://localhost:5601/app/dev_tools#/console\n\n"
                f"2. Or use curl:\n"
                f"   curl -X POST 'http://localhost:9200/{index_name}/_search' \\\n"
                f"     -H 'Content-Type: application/json' \\\n"
                f"     -d '{{\n"
                f"       \"query\": {{\n"
                f"         \"match\": {{\"_all\": \"your search query\"}}\n"
                f"       }}\n"
                f"     }}'\n\n"
                f"3. Or use Python:\n"
                f"   from opensearchpy import OpenSearch\n"
                f"   client = OpenSearch([{{'host': 'localhost', 'port': 9200}}])\n"
                f"   results = client.search(index='{index_name}', body={{\n"
                f"       'query': {{'match': {{'_all': 'your query'}}}}\n"
                f"   }})\n"
            )
        
        # UI files exist - provide launch instructions
        return (
            f"✅ Search UI ready for index '{index_name}'\n\n"
            f"To launch the UI:\n\n"
            f"1. Navigate to UI directory:\n"
            f"   cd {ui_dir}\n\n"
            f"2. Install dependencies (first time only):\n"
            f"   npm install\n\n"
            f"3. Start the UI:\n"
            f"   npm start\n\n"
            f"4. Open in browser:\n"
            f"   http://{SEARCH_UI_HOST}:{ui_port}\n\n"
            f"The UI will connect to OpenSearch at localhost:9200\n"
            f"and search against index '{index_name}'\n"
        )
    
    except Exception as e:
        return f"Error preparing search UI: {e}"
