## solveathon26

# Grant Opportunity Finder

A web application that helps individuals and organizations discover relevant federal grant opportunities from Grants.gov. The platform combines keyword-based search, a guided matching quiz, and a preference-learning discovery flow to surface funding opportunities tailored to each user's project context.

Link to Website: https://eam-gqh8h9fsa9guadft.eastus2-01.azurewebsites.net/
Link to Presentation: https://docs.google.com/presentation/d/1MsYq_bKNuNgZE4QTeHOYFKtcAlSUFJo8eXrLkpdfURc/edit?usp=sharing
---

## Features

### Keyword Search

Search for open grants using keywords or comma-separated terms. Results are pulled live from the Grants.gov API and can be filtered by:

- **Keywords** — any terms relevant to your project or focus area
- **Award amount** — set a minimum and maximum to narrow results to grants within your funding range
- **Deadline visibility** — each result displays a visual timeline bar and badge indicating how much time remains before the grant closes

### Grant Matching Quiz

A guided four-step form that helps users find relevant grants based on their specific project context. Users provide:

- **Organization type** — nonprofit, government agency, university, small business, tribal nation, or individual
- **Project focus** — aligned with NC OSBM strategic policy areas: education, health and human services, justice and public safety, environment and natural resources, economic development, transportation, housing, or technology
- **Project description and location** — free-text fields used to extract additional search terms
- **Funding range** — under $50k, $50k–$250k, $250k–$1M, or over $1M

Results are scored by relevance against the user's inputs and classified as Full Matches or Close Matches. An AI-generated explanation is provided for each of the top results, describing specifically how the grant aligns with the user's organization, focus area, and project description.

### Grant Speed Dating

A card-based discovery flow for users who are uncertain about what they are looking for. Users are presented with a series of grant cards one at a time and indicate whether each is of interest. After five rounds, the application:

- Derives a preference profile from the user's responses, weighted by agency, category, award size, and recurring keywords
- Scores the remaining grant pool against that profile
- Returns the top fifteen personalized recommendations ranked by match strength

### AI-Powered Explanations

Powered by Azure OpenAI, the application generates a concise plain-language explanation for each top quiz result. The explanation describes how the grant aligns with the user's stated organization type, focus area, project description, and location, and notes where the fit may be limited.

### Match Criteria Classification

Every result is assigned a match classification based on how completely it satisfies the user's inputs:

- **Full match** — the grant's text satisfies all of the user's search criteria
- **Strong match** — high relevance score relative to the top result
- **Good match** — moderate relevance
- **Possible match** — loosely related; may be worth reviewing

Grants that fall outside the user's selected funding range are excluded from results entirely. Grants with no published award data are retained, as eligibility cannot be confirmed or ruled out.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.9, Flask |
| Data source | Grants.gov Search2 API, fetchOpportunity API |
| AI | Azure OpenAI via Microsoft Foundry |
| Data refresh | Azure Functions (timer trigger, every 6 hours) |
| Blob storage | Azure Blob Storage |
| Frontend | Jinja2 templates, HTML, CSS, JavaScript |
| Deployment | Azure App Service (planned) |

---

## Getting Started

### Prerequisites

- Python 3.9 or higher
- An Azure subscription with an Azure OpenAI deployment

### Installation

```bash
# Clone the repository
git clone https://github.com/your-org/solveathon26.git
cd solveathon26

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate  # Mac/Linux

# Install dependencies
pip install -r requirements.txt
```

### Environment Variables

Create a `.env` file in the project root with the following values:

```
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_KEY=your-api-key
AZURE_OPENAI_DEPLOYMENT=your-deployment-name
SECRET_KEY=any-long-random-string
```

The `.env` file is listed in `.gitignore` and must not be committed to version control.

### Running Locally

```bash
PORT=5001 python app.py
```

Open `http://127.0.0.1:5001` in your browser.

---

## Project Structure

```
solveathon26/
├── app.py                  # Main Flask application
├── function_app.py         # Azure Function for scheduled grant data refresh
├── requirements.txt        # Python dependencies
├── .env                    # Local environment variables (not committed)
├── templates/
│   ├── index.html          # Main search page
│   ├── quiz.html           # Four-step quiz form
│   ├── quiz_results.html   # Quiz results with AI explanations
│   ├── date_start.html     # Speed dating introduction screen
│   ├── date_card.html      # Individual grant card view
│   └── date_results.html   # Personalized recommendations
└── data/
    └── opportunities.json  # Locally cached grant data (optional)
```

---

## Azure Function

`function_app.py` runs on a scheduled timer trigger every six hours. It searches Grants.gov for all configured keywords, deduplicates results, and saves a JSON snapshot to Azure Blob Storage at `grants-data/opportunities-latest.json`.

To deploy, configure the `AzureWebJobsStorage` connection string in your Function App application settings.

---

## Contributing

1. Branch from `main`
2. Make and test your changes locally
3. Open a pull request against `main`

Do not commit `.env` or any file containing API keys or secrets.
