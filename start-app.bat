@echo off

:: Start the backend server
start cmd /k "cd backend && python app.py"

:: Start the frontend development server
start cmd /k "pnpm dev"