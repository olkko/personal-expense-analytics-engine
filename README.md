# personal-expense-analytics-engine
Python application for automated expense tracking, categorization, and financial analytics.


The goal is to develop a Python-based web application that helps users understand their spending habits and identify potential savings opportunities. The application will retrieve transaction data through a Polish bank's Open Banking API, process and categorize expenses, detect recurring payments and unusual spending patterns, and provide AI-powered financial insights and personalized savings recommendations. Users will be able to access the application through a web browser on both computers and smartphones.

The main technologies we plan to use are Python, Streamlit, pandas, SQLite, Plotly, pytest, and an AI model or API for generating spending insights. We will also investigate the availability of banking API sandbox environments(as direct access to real transaction data is restricted) and their access requirements.

**Proposed implementation plan:**

- **Session 1 – Project setup and architecture:** Define requirements, create the GitHub repository, design the application architecture, and implement the transaction data model.
- **Session 2 – Banking API integration:** Investigate available Polish Open Banking APIs, configure a suitable sandbox environment, and implement transaction retrieval and authentication where possible.
- **Session 3 – Transaction processing:** Normalize transaction data, detect duplicates, categorize expenses, and identify recurring payments.
- **Session 4 – Database and analytics:** Implement SQLite storage, calculate spending statistics, analyze monthly trends, and detect unusual spending patterns.
- **Session 5 – Technical presentation:** Present the project architecture, technologies, API integration approach, AI components, and implementation progress.
- **Session 6 – Web application and AI features:** Develop the browser interface, implement interactive visualizations, and integrate AI-generated spending insights and personalized savings recommendations based on calculated financial statistics.
- **Session 7 – Testing and finalization:** Add automated tests, improve error handling and logging, validate the AI-generated insights, finalize the documentation, and demonstrate the completed application.

