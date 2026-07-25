from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, AIMessage
from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent
from langgraph.graph import StateGraph, START, END
import logging
import uvicorn
import json

# Configure logging for debugging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Enable CORS for cross-platform access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For development - restrict in production
    #allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Define your tools
@tool
def weather(city: str) -> str:
    """Get the weather in a given city"""
    logger.info(f"Weather tool called for city: {city}")
    return f"The weather in {city} is sunny and warm (25°C)"


@tool
def calculate(operation: str) -> str:
    """Perform a basic calculation. Operation should be like 'add 5 10' or 'multiply 3 4'"""
    logger.info(f"Calculate tool called with: {operation}")
    try:
        parts = operation.split()
        if len(parts) < 3:
            return "Invalid operation format. Use 'add 5 10' or 'multiply 3 4'"

        op = parts[0].lower()
        num1 = float(parts[1])
        num2 = float(parts[2])

        if op == "add":
            result = num1 + num2
        elif op == "subtract":
            result = num1 - num2
        elif op == "multiply":
            result = num1 * num2
        elif op == "divide":
            if num2 == 0:
                return "Cannot divide by zero"
            result = num1 / num2
        else:
            return f"Unknown operation: {op}"

        return f"Result: {num1} {op} {num2} = {result}"
    except Exception as e:
        return f"Calculation error: {str(e)}"


tools = [weather, calculate]

# Initialize the LLM
llm = ChatOllama(model="qwen3:8b", temperature=0.7)

# Create agent - using the correct LangGraph API
try:
    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt="You are a helpful AI assistant that has access to certain tools. Use the tools to help the user with their tasks. Always explain what you're doing and why."
    )
    logger.info("Agent created successfully")
except Exception as e:
    logger.error(f"Error creating agent: {str(e)}")
    raise


# Request/Response models
class ChatRequest(BaseModel):
    message: str
    history: Optional[List[dict]] = None


class ChatResponse(BaseModel):
    response: str
    tool_calls: Optional[List[dict]] = []


@app.get("/")
async def root():
    """Health check endpoint"""
    return {"message": "AI Assistant API is running", "status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Chat endpoint that processes user messages and returns AI responses
    """
    try:
        logger.info(f"Received message: {request.message}")
        print(request.message)
        # Build message history
        messages = []
        if request.history:
            for msg in request.history:
                if msg.get("role") == "user":
                    messages.append(HumanMessage(content=msg.get("content", "")))
                elif msg.get("role") == "assistant":
                    messages.append(AIMessage(content=msg.get("content", "")))

        # Add current user message
        messages.append(HumanMessage(content=request.message))

        logger.info(f"Invoking agent with {len(messages)} messages")

        # Invoke the agent
        # The correct input key for react agents is typically "messages"
        result = agent.invoke({"messages": messages})

        logger.info(f"Agent result: {result}")

        # Extract the response from the result
        # For LangGraph ReAct agents, the output is in result.get("messages", [])
        response_messages = result.get("messages", [])

        final_response = ""
        tool_calls = []

        if response_messages:
            # Get the last message content
            last_msg = response_messages[-1]

            # Handle different message types
            if hasattr(last_msg, 'content'):
                final_response = last_msg.content
            else:
                final_response = str(last_msg)

            # Extract tool calls from all messages
            for msg in response_messages:
                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    for call in msg.tool_calls:
                        # Handle different tool call formats
                        if isinstance(call, dict):
                            tool_calls.append({
                                "name": call.get('name', call.get('tool', 'unknown')),
                                "args": call.get('args', call.get('input', {}))
                            })
                        else:
                            # Convert object to dict if needed
                            tool_calls.append({
                                "name": getattr(call, 'name', getattr(call, 'tool', 'unknown')),
                                "args": getattr(call, 'args', getattr(call, 'input', {}))
                            })

        logger.info(f"Response: {final_response}")
        logger.info(f"Tool calls: {tool_calls}")

        return ChatResponse(
            response=final_response,
            tool_calls=tool_calls
        )

    except HTTPException as http_err:
        logger.error(f"HTTP Error: {http_err.detail}")
        raise http_err
    except Exception as e:
        logger.error(f"Error processing message: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": str(e),
                "type": type(e).__name__,
                "message": "An error occurred while processing your request. Check server logs for details."
            }
        )


@app.get("/health")
async def health_check():
    """Detailed health check endpoint"""
    return {
        "status": "healthy",
        "llm_model": "tinyllama",
        "tools_available": [tool.name for tool in tools],
        "agent_ready": True
    }


if __name__ == "__main__":
    logger.info("Starting AI Assistant API...")
    uvicorn.run(app, host="0.0.0.0", port=8000)