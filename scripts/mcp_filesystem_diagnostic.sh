#!/bin/bash
# MCP Filesystem Diagnostic Script
# Run this on your Raspberry Pi to diagnose the filesystem access issue

echo "============================================================"
echo "MCP Filesystem Diagnostic"
echo "============================================================"
echo

echo "1. Checking MCP Server Process..."
if pgrep -f "mcp.*server" > /dev/null; then
    echo "✓ MCP server is running"
    pgrep -af "mcp.*server"
else
    echo "✗ MCP server is NOT running"
fi
echo

echo "2. Checking Claude Code Process..."
if pgrep -f "claude.*code" > /dev/null; then
    echo "✓ Claude Code is running"
    pgrep -af "claude.*code"
else
    echo "✗ Claude Code is NOT running"
fi
echo

echo "3. Checking MCP Server Configuration..."
if [ -f ~/.config/claude-code/mcp.json ]; then
    echo "✓ Found MCP config at ~/.config/claude-code/mcp.json"
    echo "Filesystem server configuration:"
    cat ~/.config/claude-code/mcp.json | jq '.mcpServers.filesystem' 2>/dev/null || \
        grep -A 10 "filesystem" ~/.config/claude-code/mcp.json
elif [ -f ~/.claude/mcp.json ]; then
    echo "✓ Found MCP config at ~/.claude/mcp.json"
    cat ~/.claude/mcp.json | jq '.mcpServers.filesystem' 2>/dev/null
else
    echo "✗ No MCP config file found"
fi
echo

echo "4. Checking if /home/user/Downloads exists..."
if [ -d /home/user/Downloads ]; then
    echo "✓ /home/user/Downloads exists"
    ls -la /home/user/Downloads/*.py 2>/dev/null || echo "  (no .py files found)"
else
    echo "✗ /home/user/Downloads does NOT exist"
fi
echo

echo "5. Checking Docker containers..."
if command -v docker >/dev/null 2>&1; then
    echo "✓ Docker is installed"
    echo "Running containers:"
    docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Image}}" 2>/dev/null || echo "  (unable to list - may need sudo)"
else
    echo "✗ Docker not found"
fi
echo

echo "============================================================"
echo "Recommendations:"
echo "============================================================"
echo
echo "If MCP server is NOT running:"
echo "  → Restart it with: npx @modelcontextprotocol/server-filesystem /home/user"
echo
echo "If Claude Code is NOT running:"
echo "  → Start it: claude-code"
echo
echo "If configuration is missing filesystem mount:"
echo "  → Edit ~/.config/claude-code/mcp.json"
echo "  → Add filesystem server with /home/user directory"
echo
echo "If everything looks good but still not working:"
echo "  → Restart Claude Code completely"
echo "  → Check the conversation session - may need to refresh"
echo
