# AI Agent Project 🤖🚀

An intelligent, autonomous AI Agent application running locally inside **Termux on Android**, powered by Python. This agent is designed to interact with the Android subsystem via Termux, execute terminal commands safely, maintain persistent chat histories, and provide a seamless web-based chat interface.

---

## 📂 Project Structure & Architecture

Here is a detailed breakdown of the files and directories in this project:

*   **`app.py`**: The main entry point of the application. It hosts a local web server (using Flask/FastAPI) and exposes endpoints for the web UI. It bridges the frontend interface with the core AI engine.
*   **`chat.py`**: The heart of the AI Agent. Contains the main logic for processing user prompts, formatting system instructions, managing tool execution (interpreting and executing `RUN_CMD` instructions safely), and interacting with the LLM API.
*   **`save_chat.py`**: Implements the logic to persist chat logs, conversation state, and history onto the local storage, enabling users to resume past sessions.
*   **`test_chat_history.py`**: A test suite designed to verify the correct behavior of chat history persistence, state tracking, and recovery.
*   **`templates/`**: Holds HTML templates for the web interface, rendering a modern, responsive chat room.
*   **`static/`**: Contains CSS and JavaScript files that style the chat UI and handle dynamic AJAX/WebSocket requests to the backend.
*   **`data/`**: A local storage directory where conversation JSON files, SQLite databases, or local application states are securely saved.
*   **`requirements.txt`**: Contains all necessary Python dependencies (e.g., Flask, requests, google-generativeai, etc.) required to run this project.

---

## 🛠️ Key Features

*   **Termux Integration**: Specially optimized to run inside Termux, allowing direct system-level administration on Android (with user consent).
*   **Dynamic Shell Execution**: Capable of running terminal commands and parsing real-time outputs to solve complex administrative or development tasks.
*   **Local Web UI**: A beautiful, user-friendly chat interface hosted locally, accessible via any browser on your Android device (usually at `http://127.0.0.1:5000`).
*   **Session Persistence**: Automatically saves and restores chat history, ensuring you never lose context.
*   **Modular Architecture**: Clean separation of concerns between web rendering, LLM execution, command safety, and local storage.

---

## ⚙️ Installation & Setup

Follow these steps to set up and run the AI Agent on your Android device:

### 1. Clone the Repository
```bash
git clone https://github.com/tusharsharmalogic-cmyk/Ai-agent.git
cd Ai-agent
```

### 2. Install Dependencies
Ensure you have Python installed in Termux, then run:
```bash
pip install -r requirements.txt
```

### 3. Run the Application
Start the local server:
```bash
python app.py
```
Now, open your mobile browser and navigate to the local address provided (e.g., `http://localhost:5000` or `http://127.0.0.1:5000`).

---

## 🔒 Safety & Best Practices
*   **Dynamic Command safety**: Ensure you review commands executed by the agent.
*   **Local Data storage**: All chat history remains locally on your device in the `/sdcard/Ai-agent/data` directory.
