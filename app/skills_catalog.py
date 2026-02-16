"""Curated skill catalog and lightweight autocomplete search."""

from __future__ import annotations

SKILLS: list[str] = [
    # Web
    "HTML",
    "CSS",
    "Sass",
    "Less",
    "Tailwind CSS",
    "Bootstrap",
    "JavaScript",
    "TypeScript",
    "React",
    "Next.js",
    "Vue.js",
    "Nuxt.js",
    "Angular",
    "Svelte",
    "SvelteKit",
    "jQuery",
    "Redux",
    "Zustand",
    "React Query",
    "Node.js",
    "Express.js",
    "NestJS",
    "Fastify",
    "Django",
    "Flask",
    "FastAPI",
    "Ruby on Rails",
    "Laravel",
    "Symfony",
    "ASP.NET Core",
    "PHP",
    "Python",
    "C#",
    "Java",
    "Go",
    "GraphQL",
    "Apollo GraphQL",
    "REST API",
    "WebSockets",
    "Socket.IO",
    "Microservices",
    "Server-Side Rendering",
    "JAMstack",
    "Webpack",
    "Vite",
    "Babel",
    "ESLint",
    "Prettier",
    "WordPress",
    "Shopify",
    "WooCommerce",
    "Magento",
    "Webflow",
    "Framer",
    "Contentful",
    "Strapi",
    "Sanity",
    "Headless CMS",
    "SEO",
    "Technical SEO",
    "Schema Markup",
    "Google Tag Manager",
    "Google Analytics 4",
    "Conversion Rate Optimization",
    "A/B Testing",
    # Mobile
    "Android Development",
    "iOS Development",
    "Kotlin",
    "Java for Android",
    "Swift",
    "SwiftUI",
    "Objective-C",
    "React Native",
    "Flutter",
    "Dart",
    "Ionic",
    "Capacitor",
    "Xamarin",
    "Firebase",
    "Mobile UI Design",
    "Mobile App Testing",
    "Push Notifications",
    "In-App Purchases",
    "App Store Optimization",
    # Data
    "SQL",
    "PostgreSQL",
    "MySQL",
    "SQLite",
    "Microsoft SQL Server",
    "Oracle Database",
    "MongoDB",
    "Redis",
    "Elasticsearch",
    "BigQuery",
    "Snowflake",
    "Amazon Redshift",
    "Data Modeling",
    "Data Warehousing",
    "ETL",
    "ELT",
    "Data Pipeline",
    "Apache Airflow",
    "dbt",
    "Apache Kafka",
    "Apache Spark",
    "Pandas",
    "NumPy",
    "Polars",
    "Jupyter Notebook",
    "Data Cleaning",
    "Data Analysis",
    "Data Visualization",
    "Tableau",
    "Power BI",
    "Looker Studio",
    "Excel",
    "Google Sheets",
    "Statistics",
    "Business Intelligence",
    # DevOps
    "Linux",
    "Bash",
    "Shell Scripting",
    "Git",
    "GitHub",
    "GitLab",
    "CI/CD",
    "GitHub Actions",
    "GitLab CI",
    "Jenkins",
    "CircleCI",
    "Docker",
    "Docker Compose",
    "Kubernetes",
    "Helm",
    "Terraform",
    "Ansible",
    "Puppet",
    "Chef",
    "Nginx",
    "Apache HTTP Server",
    "AWS",
    "Amazon EC2",
    "Amazon S3",
    "Amazon RDS",
    "AWS Lambda",
    "CloudFront",
    "Google Cloud Platform",
    "Microsoft Azure",
    "DigitalOcean",
    "Cloudflare",
    "Server Administration",
    "Infrastructure as Code",
    "Observability",
    "Prometheus",
    "Grafana",
    "Sentry",
    "Datadog",
    "OpenTelemetry",
    "System Design",
    "Performance Optimization",
    "Site Reliability Engineering",
    "Zero Downtime Deployment",
    # AI
    "Machine Learning",
    "Deep Learning",
    "TensorFlow",
    "PyTorch",
    "Scikit-learn",
    "XGBoost",
    "LightGBM",
    "Natural Language Processing",
    "Computer Vision",
    "Reinforcement Learning",
    "MLOps",
    "Feature Engineering",
    "Model Deployment",
    "Model Evaluation",
    "Prompt Engineering",
    "LLM Integration",
    "RAG",
    "LangChain",
    "LlamaIndex",
    "OpenAI API",
    "Hugging Face",
    "Vector Databases",
    "Pinecone",
    "Weaviate",
    "FAISS",
    "Whisper",
    "Speech-to-Text",
    "Text-to-Speech",
    "Chatbot Development",
    "AI Automation",
    # Design
    "UI Design",
    "UX Design",
    "Product Design",
    "Interaction Design",
    "Wireframing",
    "Prototyping",
    "Figma",
    "Adobe XD",
    "Sketch",
    "Design Systems",
    "Usability Testing",
    "User Research",
    "Information Architecture",
    "Visual Design",
    "Brand Identity",
    "Logo Design",
    "Typography",
    "Color Theory",
    "Icon Design",
    "Illustration",
    "Adobe Photoshop",
    "Adobe Illustrator",
    "Adobe InDesign",
    "Canva",
    "Presentation Design",
    "Pitch Deck Design",
    "Landing Page Design",
    "Email Design",
    "Social Media Design",
    # QA
    "Quality Assurance",
    "Manual Testing",
    "Automated Testing",
    "Unit Testing",
    "Integration Testing",
    "End-to-End Testing",
    "Regression Testing",
    "Performance Testing",
    "Load Testing",
    "Security Testing",
    "API Testing",
    "Test Planning",
    "Test Case Design",
    "Bug Tracking",
    "Selenium",
    "Cypress",
    "Playwright",
    "JUnit",
    "Pytest",
    "Postman",
    # Writing
    "Content Writing",
    "Copywriting",
    "Technical Writing",
    "Blog Writing",
    "SEO Writing",
    "Ghostwriting",
    "Editing",
    "Proofreading",
    "UX Writing",
    "Email Copywriting",
    "Sales Copy",
    "Scriptwriting",
    "Product Descriptions",
    "Case Study Writing",
    "White Paper Writing",
    "Grant Writing",
    "Resume Writing",
    "LinkedIn Profile Writing",
    "Press Release Writing",
    "Translation",
]


def normalize(s: str) -> str:
    return " ".join(s.lower().strip().split())


def search_skills(query: str, limit: int = 12) -> list[str]:
    normalized_query = normalize(query)
    safe_limit = max(0, int(limit))
    if safe_limit == 0:
        return []
    if not normalized_query:
        return SKILLS[:safe_limit]

    query_tokens = normalized_query.split()
    ranked: list[tuple[tuple[int, int, int, int, int], str]] = []
    seen: set[str] = set()

    for index, skill in enumerate(SKILLS):
        if skill in seen:
            continue
        seen.add(skill)

        normalized_skill = normalize(skill)
        skill_tokens = normalized_skill.split()

        matched_tokens_count = 0
        prefix_token_matches = 0
        first_match_position: int | None = None

        for query_token in query_tokens:
            token_prefix_hit = any(skill_token.startswith(query_token) for skill_token in skill_tokens)
            contains_hit = query_token in normalized_skill
            token_matched = token_prefix_hit or contains_hit
            if token_matched:
                matched_tokens_count += 1
            if token_prefix_hit:
                prefix_token_matches += 1
            if token_matched:
                pos = normalized_skill.find(query_token)
                if pos >= 0 and (first_match_position is None or pos < first_match_position):
                    first_match_position = pos

        if len(query_tokens) == 1:
            if matched_tokens_count != 1:
                continue
        elif matched_tokens_count < 1:
            continue

        if first_match_position is None:
            first_match_position = len(normalized_skill)
        full_query_prefix = 1 if normalized_skill.startswith(normalized_query) else 0

        # Sort by strongest semantic hits first, then by earlier/shorter strings, then source order.
        score = (
            -matched_tokens_count,
            -prefix_token_matches,
            -full_query_prefix,
            first_match_position,
            len(normalized_skill),
            index,
        )
        ranked.append((score, skill))

    ranked.sort(key=lambda item: item[0])
    return [skill for _, skill in ranked[:safe_limit]]
