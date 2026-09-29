"""
Plain-English page content: the beginner guide, glossary and FAQ.

Kept as data (not hard-coded in the template) so the same FAQ text feeds both
the visible FAQ section and the FAQPage structured data for search engines.
Search engines expect those two to match word for word.
"""
from __future__ import annotations

BRAND = "TagTrace"
TAGLINE = "AI answers from UK annual reports, traced to the filing's own tags"
GITHUB_URL = "https://github.com/NikhilRana2076/ixbrl-qa-demo"
PORTFOLIO_URL = "https://nikhilrana.com.np"

# Steps to get a filing. Checked against the Companies House filing-history page.
GET_A_FILING = [
    ("Search the company",
     "Go to Find and update company information on GOV.UK and search the company's name or number. It's free and you don't need an account."),
    ("Open Filing history",
     "On the company page, open the Filing history tab. You can filter the list to show Accounts only."),
    ("Download iXBRL",
     "Next to a set of accounts, click Download iXBRL. You get an .xhtml file. If there's only a View PDF link, those accounts weren't filed digitally and won't work here."),
    ("Upload it here",
     "Drag the file into the upload box and ask your question in plain English."),
]

FCA_NSM_URL = "https://data.fca.org.uk/#/nsm/nationalstoragemechanism"

# Listed companies publish tagged annual reports (ESEF) on the FCA's register,
# which is often the easier place to find a well-known company's accounts.
GET_FROM_FCA = [
    ("Open the FCA register",
     "Go to the FCA's National Storage Mechanism (NSM). It's free and needs no account."),
    ("Search and filter",
     "Search the company's name and set the ESEF AFR type filter to Tagged."),
    ("Download and upload",
     "Download the annual financial report (an .xhtml file or a .zip report package) and upload it here."),
]

USE_CASES = [
    ("Check a business you deal with",
     "A supplier, customer, landlord, or a company you have an interview with. How big is it, is it profitable, how much cash does it hold?"),
    ("Learn to read company accounts",
     "Every answer shows which line of the accounts it came from, so you learn what each term means as you go."),
    ("Research and journalism",
     "Pull a figure quickly, with the exact tag, period and filing it came from, so it can be cited and checked."),
    ("Accountants and analysts",
     "A fast first look-up across a long report. Always confirm against the filing before relying on it."),
    ("People working on AI",
     "See one practical way to stop a language model making up numbers: make it cite evidence, then check the citation in code."),
]

STARTER_QUESTIONS = [
    "What was turnover for the year?",
    "What was profit before tax?",
    "How much cash did the company have at the year end?",
    "What were net assets at the balance sheet date?",
    "How many people did the company employ on average?",
    "How did profit change compared with last year?",
    "Summarise the going concern disclosure.",
]

GLOSSARY = [
    ("iXBRL filing",
     "An annual report saved as a web page with hidden labels (\"tags\") on every number. Most UK companies file their accounts with Companies House this way, so a computer can read each figure exactly."),
    ("Tag / concept",
     "The label on a number, for example ProfitLossOnOrdinaryActivitiesBeforeTax. TagTrace shows it on every answer so you can see exactly which figure was used."),
    ("Revenue (turnover)",
     "Money earned from selling goods or services, before any costs are taken off."),
    ("Gross profit",
     "Revenue minus the direct cost of what was sold."),
    ("Operating profit",
     "Profit from the main business after running costs such as wages and rent, before interest and tax."),
    ("Profit before tax",
     "Profit after all costs and interest, before corporation tax. A common headline figure."),
    ("Profit for the year",
     "What's left after tax. It can be negative, which is a loss."),
    ("Net assets (equity)",
     "Everything the company owns minus everything it owes, on the balance sheet date."),
    ("“Year ended” vs “as at”",
     "Profit figures cover a period (“year ended 31 December 2025”). Balance sheet figures such as cash or net assets are a snapshot on one day (“as at 31 December 2025”)."),
    ("Prior year / comparative",
     "Accounts show last year's figures next to this year's. TagTrace tells you which year every answer comes from."),
    ("Dimension",
     "A breakdown of a figure, such as goodwill as one class of intangible assets, or one business segment. The same concept can appear several times with different dimensions."),
    ("Consolidated vs company",
     "A group's accounts can show the whole group (consolidated) and the parent company on its own. They're different numbers."),
    ("FRS 102 / IFRS",
     "The two sets of accounting rules used in the UK. Most private companies use FRS 102 (UK GAAP). Listed groups use IFRS."),
    ("Scale",
     "Filings often show “£m” or “£000”. The tag records the real size, so TagTrace can show £3.2m and the exact £3,165,099."),
]


def faq(public_limit: int, max_upload_mb: int, idle_minutes: int) -> list[tuple[str, str]]:
    """Question/answer pairs. Plain text only: it is also used in JSON-LD."""
    return [
        ("What is TagTrace?",
         "TagTrace is a free demo that answers questions about UK company annual reports. It doesn't let the AI read the report and guess. It finds the exact tagged figures in the filing, the AI must say which ones it used, and the server checks that before showing the answer."),
        ("Do I need to know accounting to use it?",
         "No. Ask in plain English, for example “How much cash did they have?”. The guide and glossary on this page explain the terms you'll see in answers, such as profit before tax or net assets."),
        ("Where do I get a filing, and does it cost anything?",
         "Filings are free from Companies House. Search the company on Find and update company information, open Filing history, and click Download iXBRL next to a set of accounts. Accounts that only have a View PDF link were filed on paper or as a scan and can't be read here. For companies listed on the London Stock Exchange, the FCA's National Storage Mechanism is often easier: search the company, set the ESEF AFR type filter to Tagged, and download the annual report."),
        ("Why did it say “Not in tagged data”?",
         "It usually means the figure isn't in the filing. Many small UK companies are allowed to leave the profit and loss account out of what they file, so there is no turnover or profit to find, only the balance sheet. It can also mean the question used different words from the filing. Try a term from the glossary, or try the other model. Saying it can't find something is intended: the system won't make up a number."),
        ("How do I know an answer is right?",
         "Every answer card shows a badge. “Matched to tagged fact” means the value was read straight from the filing's tag. “Computed from tagged facts” means the server did the arithmetic itself. “Quote found in filing” means a narrative answer quotes the filing word for word. Each card also shows the tag, the period and any dimension, so you can find the same number in the filing."),
        ("Can it still be wrong?",
         "Yes. The server can confirm that a number really is in the filing, but not that it's the right one for your question. The AI can still pick last year's figure, or a breakdown instead of the total. That's why the period and dimension are shown on every card. Check them, and check anything important against the filing itself."),
        ("What's the difference between the two models?",
         f"GPT-5.6 Terra is free for {public_limit} questions per visit. Claude Sonnet 4.6 needs an access code. In the 102-question benchmark behind this demo, both models were given the same evidence. Claude was wrong on 5.1% of the questions it answered and GPT-5.6 Terra on 18.2%."),
        ("How do I get more questions or a Claude access code?",
         "Use the contact links at the bottom of the page and say briefly what you'd like to test. Access codes are free."),
        ("What happens to the file I upload?",
         f"The file is deleted as soon as it has been read. The figures extracted from it are kept only for your visit and deleted when you clear the filing or after {idle_minutes} minutes without activity. Your question and the matching figures are sent to OpenAI or Anthropic to write the answer. If you use the thumbs up or down on an answer, the question, the answer and your vote are logged so I can measure accuracy. Nothing identifies you. Only upload public filings, never anything confidential."),
        ("Which files work?",
         f"UK annual reports in iXBRL format: .xhtml or .html, or a report-package .zip, up to {max_upload_mb} MB. Both UK GAAP (FRS 102) and IFRS accounts work. Scanned or PDF-only accounts don't. Very large listed-company reports can exceed the size limit."),
        ("Is this financial advice?",
         "No. TagTrace is a research demonstration. Use it to find and understand figures, not to make investment, lending or credit decisions."),
        ("Who built it, and can I see the code?",
         "Nikhil Rana built TagTrace from his MSc Artificial Intelligence research at the University of West London on reducing AI hallucinations in financial reports. The code is on GitHub."),
    ]
