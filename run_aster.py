import os
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file

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
from aster_toolkit.knowledge.skill_check_hook import SkillCheckHook
from orchestral.prompts import RICH_UI_SYSTEM_PROMPT
# from orchestral.llm import Claude
from orchestral.llm import GPT

from aster_toolkit import (
    RunTaurexTransmissionModelTool,
    RunTaurexEmissionModelTool,
    SetTaurexPaths,
    SimulateTaurexRetrieval,
    WriteTaurexParameterFile,
    GetExoplanetParameters,
    DownloadDataset,
    FindExoplanetsByCondition,
    FindExcaliburTarget,
    CalculateExcaliburCrossSections,
    RunCerberusEquilibriumModel,
    RunCerberusFreeChemistryModel,
    SimulateArielObservation,
    ComputeLetheLightCurve,
    RunFastChemEquilibriumTool,
    BinSpectrum,
)

base_directory = 'workspace'
os.makedirs(base_directory, exist_ok=True)

# Excalibur runs in its own Python 3.12 env (set EXCALIBUR_PYTHON, DATA_BASEDIR and EXCALIBUR_ESP_DIR in .env)
excalibur_state = dict(
    excalibur_python=os.getenv('EXCALIBUR_PYTHON', ''),
    excalibur_data_dir=os.getenv('DATA_BASEDIR', ''),
    excalibur_esp_directory=os.getenv('EXCALIBUR_ESP_DIR', ''),
)
# LETHE light curves: optional overrides of the Excalibur settings above
lethe_state = dict(
    lethe_python=os.getenv('LETHE_PYTHON', ''),
    lethe_esp_directory=os.getenv('LETHE_ESP_DIR', ''),
    lethe_directory=os.getenv('LETHE_DIR', ''),
)

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
    RunTaurexTransmissionModelTool(base_directory=base_directory),
    RunTaurexEmissionModelTool(base_directory=base_directory),
    SimulateTaurexRetrieval(base_directory=base_directory),
    WriteTaurexParameterFile(base_directory=base_directory),

    # Data acquisition tools
    GetExoplanetParameters(),
    DownloadDataset(base_directory=base_directory),
    FindExoplanetsByCondition(base_directory=base_directory),

    # Excalibur tools
    FindExcaliburTarget(**excalibur_state),
    CalculateExcaliburCrossSections(base_directory=base_directory, **excalibur_state),
    RunCerberusEquilibriumModel(base_directory=base_directory, **excalibur_state),
    RunCerberusFreeChemistryModel(base_directory=base_directory, **excalibur_state),
    SimulateArielObservation(base_directory=base_directory, **excalibur_state),
    ComputeLetheLightCurve(base_directory=base_directory, **excalibur_state, **lethe_state),

    # Chemistry tools
    RunFastChemEquilibriumTool(base_directory=base_directory),

    # Binning tools
    BinSpectrum(base_directory=base_directory),
]

# SkillCheckHook: ASTER tools are blocked until the agent has checked the skill files (SearchKnowledgeBase first, ReadFileTool on skills/ as fallback).
hooks = [DangerousCommandHook(), SkillCheckHook(tools)]

# Load ASTER system prompt
with open('aster_system_prompt.md', 'r') as f:
    aster_prompt = f.read()

system_prompt = f'{RICH_UI_SYSTEM_PROMPT}\n\n{aster_prompt}'

agent = Agent(
    # llm=Claude(),
    llm=GPT(model="gpt-4.1-mini"),
    tools=tools,
    tool_hooks=hooks,
    system_prompt=system_prompt
)

from orchestral.ui.app import server as app_server

app_server.run_server(agent, host='localhost', port=8000)

