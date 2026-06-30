# Build the Thickness Agent into a single Windows .exe.
# Run on a Windows machine with Python 3 installed.
#   PS> .\build.ps1
# Produces dist\thickness-agent.exe
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m pip install --upgrade pyinstaller requests

pyinstaller --onefile --name thickness-agent `
  --hidden-import requests `
  thickness_agent.py

Write-Host ""
Write-Host "Built: dist\thickness-agent.exe"
Write-Host "To run as a Windows service, install with NSSM (https://nssm.cc):"
Write-Host "  nssm install ThicknessAgent C:\thickness-agent\thickness-agent.exe"
Write-Host "  nssm set ThicknessAgent AppEnvironmentExtra THICKNESS_AGENT_CONFIG=C:\ProgramData\ThicknessAgent\config.json"
Write-Host "  nssm start ThicknessAgent"
Write-Host "  # then open http://localhost:7000 to run the setup wizard"
