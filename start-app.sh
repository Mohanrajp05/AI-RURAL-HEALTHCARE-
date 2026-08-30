#!/bin/bash

# Start the backend server
cd backend && python app.py &

# Start the frontend development server
pnpm dev