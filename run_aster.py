import os
import time

from orchestral import Agent
from orchestral.tools import (
    RunCommandTool,
    WriteFileTool,
    ReadFileTool,
    EditFileTool,
    FileSearchTool,
    WebSearchTool,
    TodoWrite,
    TodoRead,
    DisplayImageTool
)
from orchestral.tools.hooks import DangerousCommandHook
from orchestral.prompts import RICH_UI_SYSTEM_PROMPT
from orchestral.llm import Claude

from aster_toolkit import (
    RunTaurexModelTool,
    SetTaurexPaths,
    SimulateTaurexRetrieval,
    GetExoplanetParameters,
    DownloadDataset
)

base_directory = 'workspace'
os.makedirs(base_directory, exist_ok=True)

tools = [
    # File and command tools
    RunCommandTool(base_directory=base_directory, persistent=True),
    WriteFileTool(base_directory=base_directory),
    ReadFileTool(base_directory=base_directory, show_line_numbers=True),
    EditFileTool(base_directory=base_directory),
    FileSearchTool(base_directory=base_directory),
    WebSearchTool(),
    TodoRead(),
    TodoWrite(initial_todos='- [ ] Sample todo item'),
    DisplayImageTool,

    # TauREx modeling tools
    SetTaurexPaths,
    RunTaurexModelTool(base_directory=base_directory),
    SimulateTaurexRetrieval(base_directory=base_directory),

    # Data acquisition tools
    GetExoplanetParameters(),
    DownloadDataset(base_directory=base_directory)
]

hooks = [DangerousCommandHook()]

system_prompt = f'{RICH_UI_SYSTEM_PROMPT}\n\nThe current date is {time.strftime("%Y-%m-%d")}'

agent = Agent(
    llm=Claude(),
    tools=tools,
    tool_hooks=hooks,
    system_prompt=system_prompt
)

from orchestral.ui.app import server as app_server

app_server.run_server(agent, host='localhost', port=8000)

