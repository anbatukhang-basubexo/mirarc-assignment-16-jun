import re
import pdfplumber
import sqlite3
import json
import requests
import os

from datetime import datetime

# Connect to SQLite and initialize tables
def init_db():
    conn = sqlite3.connect('fund_data.sqlite')
    cursor = conn.cursor()
    
    # Create tables if not exist
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Companies (
            company_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            country TEXT,
            holding_company TEXT,
            business_description TEXT,
            head_office_location TEXT,
            fund_role TEXT,
            first_completion_date TEXT,
            investment_type TEXT,
            company_ownership REAL,
            affinity_board_representation TEXT,
            transaction_value_usd REAL,
            investment_cost_usd REAL,
            fair_value_usd REAL
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS FinancialPerformance (
            performance_id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER,
            fiscal_year TEXT,
            currency TEXT,
            operating_revenue REAL,
            adjusted_operating_income REAL,
            adjusted_operating_income_margin REAL,
            adjusted_net_income_after_tax REAL,
            adjusted_net_income_after_tax_margin REAL,
            net_assets REAL,
            revenue REAL,
            revenue_growth TEXT,
            ebitda REAL,
            ebitda_growth TEXT,
            ebitda_margin REAL,
            ebit REAL,
            ebit_growth TEXT,
            ebit_margin REAL,
            net_profit_after_tax REAL,
            net_profit_after_tax_growth TEXT,
            net_profit_after_tax_margin REAL,
            capex_net REAL,
            net_debt REAL,
            net_revenue REAL,
            operating_income REAL,
            operating_income_margin REAL,
            net_income_after_tax REAL,
            net_income_after_tax_margin REAL,
            FOREIGN KEY (company_id) REFERENCES Companies(company_id)
        )
    ''')

    # Truncate old data in tables
    cursor.execute('DELETE FROM FinancialPerformance')
    cursor.execute('DELETE FROM Companies')
    conn.commit()
    return conn, cursor

# Clean numeric data
def clean_numeric(value):
    if isinstance(value, str):
        value = value.replace(',', '').replace('(', '-').replace(')', '')
        try:
            return float(value)
        except:
            return None
    return value

# Save extracted data to SQLite
def save_to_db(companies, financial_data):
    conn, cursor = init_db()
    
    for company in companies:
        cursor.execute('''
            INSERT INTO Companies (
                name, country, holding_company, business_description, head_office_location,
                fund_role, first_completion_date, investment_type, company_ownership,
                affinity_board_representation, transaction_value_usd, investment_cost_usd, fair_value_usd
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            company.get('name'), company.get('country'), company.get('holding_company'),
            company.get('business_description'), company.get('head_office_location'),
            company.get('fund_role'), company.get('first_completion_date'),
            company.get('investment_type'), company.get('company_ownership'),
            company.get('affinity_board_representation'), company.get('transaction_value_usd'),
            company.get('investment_cost_usd'), company.get('fair_value_usd')
        ))
        company_id = cursor.lastrowid
        
        # Save financial performance data
        for data in financial_data:
            if data['company_name'] == company.get('name'):
                cursor.execute('''
                    INSERT INTO FinancialPerformance (
                        company_id, fiscal_year, currency,
                        operating_revenue, adjusted_operating_income, adjusted_operating_income_margin,
                        adjusted_net_income_after_tax, adjusted_net_income_after_tax_margin, net_assets,
                        revenue, revenue_growth, ebitda, ebitda_growth, ebitda_margin,
                        ebit, ebit_growth, ebit_margin,
                        net_profit_after_tax, net_profit_after_tax_growth, net_profit_after_tax_margin,
                        capex_net, net_debt, net_revenue, operating_income, operating_income_margin,
                        net_income_after_tax, net_income_after_tax_margin
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    company_id, data.get('fiscal_year'), data.get('currency'),
                    data.get('operating_revenue'), data.get('adjusted_operating_income'), data.get('adjusted_operating_income_margin'),
                    data.get('adjusted_net_income_after_tax'), data.get('adjusted_net_income_after_tax_margin'), data.get('net_assets'),
                    data.get('revenue'), data.get('revenue_growth'), data.get('ebitda'), data.get('ebitda_growth'), data.get('ebitda_margin'),
                    data.get('ebit'), data.get('ebit_growth'), data.get('ebit_margin'),
                    data.get('net_profit_after_tax'), data.get('net_profit_after_tax_growth'), data.get('net_profit_after_tax_margin'),
                    data.get('capex_net'), data.get('net_debt'), data.get('net_revenue'), data.get('operating_income'), data.get('operating_income_margin'),
                    data.get('net_income_after_tax'), data.get('net_income_after_tax_margin')
                ))
    
    conn.commit()
    conn.close()


def extract_json_from_text(text):
    """
    Clean and extract JSON from LLM/Gemini response text.
    """
    # Remove markdown code blocks ```json ... ```
    text = re.sub(r"^```json\s*|```$", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"^```[\w]*\s*|```$", "", text.strip(), flags=re.MULTILINE)
    text = text.strip()
    # If there are multiple lines, only take from { or [
    idx = min([i for i in [text.find("{"), text.find("[")] if i != -1] or [0])
    text = text[idx:]
    return text


def classify_page_gemini(text, gemini_api_url, headers, api_key, page_number=None):
    """
    Ask Gemini if this page belongs to section 2. UPDATE ON PORTFOLIO COMPANIES,
    and if so, whether it is Company Info or Financial highlights.
    """
    prompt = (
        "Given the following page text from a PDF report, answer in JSON format with two fields: "
        "`in_portfolio_update` (true/false) and `page_type` (one of: 'company_info', 'financial_highlights', 'other'). "
        "If the page is in section '2. UPDATE ON PORTFOLIO COMPANIES', set `in_portfolio_update` to true. "
        "If the page contains company profile information (Hold company, Operating company, Fund's role...), set `page_type` to 'company_info'. "
        "If the page contains a table of 'Financial highlights' which could has info Revenue, set `page_type` to 'financial_highlights'. "
        "Otherwise, set `page_type` to 'other'.\n\n"
        f"Page text:\n{text[:2000]}"
    )
    data = {"contents": [{"parts": [{"text": prompt}]}]}
    response = None
    try:
        response = requests.post(
            f"{gemini_api_url}?key={api_key}",
            headers=headers,
            data=json.dumps(data),
            timeout=60
        )
        result = response.json()
        try:
            content = result["candidates"][0]["content"]["parts"][0]["text"]
            content = extract_json_from_text(content)
            info = json.loads(content)
            print(f"Classified page [{page_number}]:", info)
            return info.get("in_portfolio_update", False), info.get("page_type", "other")
        except Exception as e:
            print(f"ERROR parsing Gemini response:", result.get("error", {}).get("message", "")[:50])
            return False, "other"

    except Exception as e:
        print("Classification failed:", e)
        return False, "other"

# Extract company info using Gemini API
def extract_company_info_gemini(pdf_path, page_range=None):
    """
    Extract company info from PDF using Gemini API.
    Returns a list of dicts with fields matching Companies table.
    """
    GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    headers = {"Content-Type": "application/json"}

    #################################################
    ######### YOUR GEMINI API KEY HERE ##############
    #################################################
    API_KEY = ""
    if not API_KEY:
        assert API_KEY, "Please set your Gemini API key in the code."

    companies = []
    financial_data = []
    with pdfplumber.open(pdf_path) as pdf:
        pages = pdf.pages
        if page_range:
            pages = pages[page_range[0]:page_range[1]]
        for i, page in enumerate(pages):
            text = page.extract_text()
            if not text or len(text.strip()) < 50:
                continue

            # 1. Classify page
            in_update, page_type = classify_page_gemini(text, GEMINI_API_URL, headers, API_KEY, page_number=i+1)
            if not in_update or page_type == "other":
                continue

            if page_type == "company_info":
                prompt = (
                    "Extract the company profile information from the following text and return as a JSON object with these keys: "
                    "name, country, holding_company, business_description, head_office_location, fund_role, first_completion_date, "
                    "investment_type, company_ownership, affinity_board_representation, transaction_value_usd, investment_cost_usd, fair_value_usd. "
                    "For any field with a title but no value, fill in with `-`. For missing value, use null.\n\n"
                    f"{text}"
                )
                data = {"contents": [{"parts": [{"text": prompt}]}]}
                response = None
                try:
                    response = requests.post(
                        f"{GEMINI_API_URL}?key={API_KEY}",
                        headers=headers,
                        data=json.dumps(data),
                        timeout=60
                    )
                    result = response.json()
                    content = result["candidates"][0]["content"]["parts"][0]["text"]
                    content = extract_json_from_text(content)
                    company_info = json.loads(content)
                    companies.append(company_info)
                    print("SUCCESS: Extracted company info from page", company_info['name'])
                except Exception as e:
                    if response:
                        error_json = response.json()
                        message = error_json.get('error', {}).get('message', '')
                        print(f"FAILED on Geminine response:", message)
                    print(f"Gemini extraction failed on page {i+1}: \n{e}")

            elif page_type == "financial_highlights":
                prompt = (
                    "Extract all financial table data from the following text and return as a JSON object or list of objects. "
                    "The keys should be: company_name, fiscal_year, currency, "
                    "operating_revenue, adjusted_operating_income, adjusted_operating_income_margin, "
                    "adjusted_net_income_after_tax, adjusted_net_income_after_tax_margin, net_assets, "
                    "revenue, revenue_growth, ebitda, ebitda_growth, ebitda_margin, "
                    "ebit, ebit_growth, ebit_margin, "
                    "net_profit_after_tax, net_profit_after_tax_growth, net_profit_after_tax_margin, "
                    "capex_net, net_debt, net_revenue, operating_income, operating_income_margin, "
                    "net_income_after_tax, net_income_after_tax_margin. "
                    "If a value is missing, use null. If a field is not present in the table, omit it. "
                    "If there are multiple years, return a list of objects, each for one year. "
                    "Example:\n"
                    "[{\"company_name\": \"ABC\", \"fiscal_year\": \"2023\", \"currency\": \"USD\", \"operating_revenue\": 509.4, ...}]\n\n"
                    f"{text}"
                )
                data = {"contents": [{"parts": [{"text": prompt}]}]}
                response = None
                try:
                    response = requests.post(
                        f"{GEMINI_API_URL}?key={API_KEY}",
                        headers=headers,
                        data=json.dumps(data),
                        timeout=60
                    )
                    result = response.json()
                    content = result["candidates"][0]["content"]["parts"][0]["text"]
                    content = extract_json_from_text(content)
                    fin_info = json.loads(content)
                    if isinstance(fin_info, list):
                        financial_data.extend(fin_info)
                    else:
                        financial_data.append(fin_info)
                    print("SUCCESS: Extracted financial data from page", fin_info['company_name'] if isinstance(fin_info, dict) else fin_info[-1]['company_name'])
                except Exception as e:
                    if response:
                        error_json = response.json()
                        message = error_json.get('error', {}).get('message', '')
                        print(f"FAILED on Geminine response:", message)
                    print(f"Gemini financial extraction failed on page {i+1}: \n{e}")

    return companies, financial_data

# Main function
def main():
    pdf_path = 'report.pdf'
    companies, financials = extract_company_info_gemini(pdf_path, )
    save_to_db(companies, financials)
    print("Data has been extracted and saved to SQLite successfully!")

if __name__ == "__main__":
    main()
