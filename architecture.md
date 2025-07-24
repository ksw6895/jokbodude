# PDF Processing System Architecture

## System Overview

```mermaid
graph TD
    A[main.py] --> B[Find PDF Files]
    B --> C[Process Each Lesson]
    C --> D[For Each Jokbo]
    D --> E[pdf_processor.py]
    E --> F[Upload to Gemini API]
    F --> G[Analyze Single Pair]
    G --> H[Merge Results]
    H --> I[pdf_creator.py]
    I --> J[Create Output PDF]
    
    K[config.py] --> E
    L[.env] --> K
```

## Detailed Data Flow

```mermaid
sequenceDiagram
    participant User
    participant Main as main.py
    participant Processor as pdf_processor.py
    participant Gemini as Gemini API
    participant Creator as pdf_creator.py
    participant Output as Output PDF

    User->>Main: Run with arguments
    Main->>Main: Find lesson & jokbo PDFs
    
    loop For each lesson PDF
        Main->>Processor: analyze_pdfs_for_lesson()
        
        loop For each jokbo PDF
            Processor->>Processor: analyze_single_jokbo_with_lesson()
            Processor->>Gemini: Upload lesson PDF
            Processor->>Gemini: Upload jokbo PDF (one at a time)
            Gemini->>Processor: Return analysis JSON
            Processor->>Processor: Accumulate results
        end
        
        Processor->>Processor: Merge all results
        Processor->>Main: Return merged analysis
        
        Main->>Creator: create_filtered_pdf()
        Creator->>Creator: Extract lesson slides
        
        loop For each related question
            Creator->>Creator: Extract question from jokbo
            Creator->>Creator: Create explanation page
        end
        
        Creator->>Output: Save PDF
    end

    Output->>User: Filtered PDFs ready
```

## Component Architecture

```mermaid
graph LR
    subgraph Input Files
        A1[lesson/\n*.pdf]
        A2[jokbo/\n*.pdf]
    end
    
    subgraph Core Components
        B1[main.py<br/>- Orchestration<br/>- File discovery<br/>- Progress tracking]
        B2[config.py<br/>- API configuration<br/>- Model settings<br/>- Safety settings]
        B3[pdf_processor.py<br/>- PDF upload<br/>- AI analysis<br/>- Result merging]
        B4[pdf_creator.py<br/>- PDF manipulation<br/>- Question extraction<br/>- Explanation pages]
    end
    
    subgraph External Services
        C1[Gemini API<br/>gemini-2.5-pro]
    end
    
    subgraph Output
        D1[output/\nfiltered_*.pdf]
    end
    
    A1 --> B1
    A2 --> B1
    B1 --> B3
    B2 --> B3
    B3 --> C1
    B3 --> B4
    B4 --> D1
```

## PDF Creation Process

```mermaid
flowchart TD
    A[Start PDF Creation] --> B[Open Lesson PDF]
    B --> C{For each related slide}
    C --> D[Insert lesson slide page]
    D --> E{Has related questions?}
    E -->|Yes| F[For each question]
    E -->|No| C
    F --> G[Extract question from jokbo]
    G --> H[Insert cropped question]
    H --> I[Create explanation page]
    I --> J[Insert explanation with Gemini analysis]
    J --> F
    F -->|Done| C
    C -->|All slides done| K[Add summary page]
    K --> L[Save output PDF]
```

## Gemini API Configuration

### Model Settings (from config.py)

```python
GENERATION_CONFIG = {
    "temperature": 0.3,          # Low temperature for consistent results
    "top_p": 0.95,              # Nucleus sampling parameter
    "top_k": 40,                # Top-k sampling parameter
    "max_output_tokens": 100000, # Maximum output tokens (very high)
    "response_mime_type": "application/json"  # Force JSON response
}

Model: gemini-2.5-pro
```

### Safety Settings

All safety categories are set to `BLOCK_NONE` to prevent content blocking:
- HARM_CATEGORY_HARASSMENT
- HARM_CATEGORY_HATE_SPEECH
- HARM_CATEGORY_SEXUALLY_EXPLICIT
- HARM_CATEGORY_DANGEROUS_CONTENT

### API Usage Pattern

1. **Upload Pattern**: One lesson PDF + One jokbo PDF at a time
2. **Request Frequency**: Sequential processing (one jokbo at a time)
3. **File Management**: Uploaded files are deleted after processing
4. **Error Handling**: Retry logic for file processing states

### Token Limits and Constraints

- **Max Output Tokens**: 100,000 tokens (configured)
- **Input Size**: Limited by PDF file upload size
- **Processing Time**: 2-second polling interval for file upload status
- **Concurrent Uploads**: Not used - sequential processing only

### Response Format

The API is configured to return JSON with this structure:
```json
{
  "related_slides": [{
    "lesson_page": number,
    "related_jokbo_questions": [{
      "jokbo_filename": string,
      "jokbo_page": number,
      "question_number": number,
      "question_text": string,
      "answer": string,
      "explanation": string,
      "relevance_reason": string
    }],
    "importance_score": 1-10,
    "key_concepts": [string]
  }],
  "summary": {
    "total_related_slides": number,
    "total_questions": number,
    "key_topics": [string],
    "study_recommendations": string
  }
}
```