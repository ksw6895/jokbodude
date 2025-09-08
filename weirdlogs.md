
2025-09-08 17:13:05 - pdf_processor.parsers.response_parser - INFO - Sanitized jokbo response: 2 pages, 2 questions
[2025-09-08 17:13:05,902: INFO/ForkPoolWorker-2] HTTP Request: DELETE https://generativelanguage.googleapis.com/v1beta/files/y57la70oon30 "HTTP/1.1 200 OK"
2025-09-08 17:13:05 - pdf_processor.api.client - INFO - Deleted file: 족보_Copy of 1728004686_193__230911_본1_치료의_기본_정답.pdf [key=k4:***_8Bo]
2025-09-08 17:13:05 - pdf_processor.api.file_manager - INFO - Deleted file: 족보_Copy of 1728004686_193__230911_본1_치료의_기본_정답.pdf
[2025-09-08 17:13:06,589: INFO/ForkPoolWorker-2] HTTP Request: DELETE https://generativelanguage.googleapis.com/v1beta/files/tk1debvvel5q "HTTP/1.1 200 OK"
2025-09-08 17:13:06 - pdf_processor.api.client - INFO - Deleted file: 강의자료_tmp6piauog_.pdf [key=k4:***_8Bo]
2025-09-08 17:13:06 - pdf_processor.api.file_manager - INFO - Deleted file: 강의자료_tmp6piauog_.pdf
2025-09-08 17:13:06 - pdf_processor.parsers.response_parser - INFO - Sanitized jokbo response: 2 pages, 2 questions
2025-09-08 17:13:06 - pdf_processor.analyzers.base - INFO - Result summary [k4:***_8Bo]: 2 pages, 2 questions
2025-09-08 17:13:06 - pdf_processor.api.multi_api_manager - INFO - Operation successful with API key 3 (***_8Bo)
2025-09-08 17:17:32 - pdf_processor.api.client - ERROR - Content generation failed (attempt 1/1) [key=k1:***ZqkU]: Generation timed out after 300s
2025-09-08 17:17:32 - pdf_processor.analyzers.base - ERROR - Generation failed on attempt 2/3: Failed to generate content after 1 attempts: Generation timed out after 300s
[2025-09-08 17:17:32,542: INFO/ForkPoolWorker-2] AFC is enabled with max remote calls: 10.
[2025-09-08 17:17:42,956: INFO/ForkPoolWorker-2] HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent "HTTP/1.1 200 OK"
[2025-09-08 17:17:43,010: INFO/ForkPoolWorker-2] AFC remote call 1 is done.
2025-09-08 17:22:32 - pdf_processor.api.client - ERROR - Content generation failed (attempt 1/1) [key=k1:***ZqkU]: Generation timed out after 300s
2025-09-08 17:22:32 - pdf_processor.analyzers.base - ERROR - Generation failed on attempt 3/3: Failed to generate content after 1 attempts: Generation timed out after 300s
[2025-09-08 17:22:32,990: INFO/ForkPoolWorker-2] HTTP Request: DELETE https://generativelanguage.googleapis.com/v1beta/files/2rzbcu5r10ch "HTTP/1.1 200 OK"
2025-09-08 17:22:32 - pdf_processor.api.client - INFO - Deleted file: 족보_Copy of 1728004686_193__230911_본1_치료의_기본_정답.pdf [key=k1:***ZqkU]
2025-09-08 17:22:32 - pdf_processor.api.file_manager - INFO - Deleted file: 족보_Copy of 1728004686_193__230911_본1_치료의_기본_정답.pdf
[2025-09-08 17:22:33,041: INFO/ForkPoolWorker-2] HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent "HTTP/1.1 200 OK"
[2025-09-08 17:22:33,042: INFO/ForkPoolWorker-2] AFC remote call 1 is done.
[2025-09-08 17:22:33,483: INFO/ForkPoolWorker-2] HTTP Request: DELETE https://generativelanguage.googleapis.com/v1beta/files/vgf9dimi6i6s "HTTP/1.1 200 OK"
2025-09-08 17:22:33 - pdf_processor.api.client - INFO - Deleted file: 강의자료_tmpyj1cmrqh.pdf [key=k1:***ZqkU]
2025-09-08 17:22:33 - pdf_processor.api.file_manager - INFO - Deleted file: 강의자료_tmpyj1cmrqh.pdf
2025-09-08 17:22:33 - pdf_processor.analyzers.jokbo_centric - ERROR - Analysis failed: Failed to generate content after 1 attempts: Generation timed out after 300s
2025-09-08 17:22:33 - pdf_processor.api.multi_api_manager - ERROR - API key 0 (***ZqkU) failed: Failed to analyze PDFs: Failed to generate content after 1 attempts: Generation timed out after 300s
2025-09-08 17:22:34 - pdf_processor.api.multi_api_manager - ERROR - Task failed: All API attempts failed after 3 unique tries. Errors: API 1: Failed to analyze PDFs: Failed to generate content after 1 attempts: Generation timed out after 300s; API 4: Failed to analyze PDFs: Failed to generate content after 1 attempts: 503 UNAVAILABLE. {'error': {'code': 503, 'message': 'The model is overloaded. Please try again later.', 'status': 'UNAVAILABLE'}}; API 0: Failed to analyze PDFs: Failed to generate content after 1 attempts: Generation timed out after 300s
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Merged 2 chunks into 7 pages
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Merged 1 chunks into 2 pages
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Duplicate jokbo_page entries detected in chunk merge: [3, 4, 7, 14, 36, 38]
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Merged 3 chunks into 28 pages
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Duplicate jokbo_page entries detected in chunk merge: [11, 37]
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Merged 2 chunks into 7 pages
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Merged 2 chunks into 8 pages
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Merged 1 chunks into 2 pages
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Duplicate jokbo_page entries detected in chunk merge: [16, 17, 36]
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Merged 4 chunks into 22 pages
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Duplicate jokbo_page entries detected in chunk merge: [9, 24, 29, 35, 36, 38, 40]
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Merged 4 chunks into 23 pages
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Merged 1 chunks into 2 pages
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Duplicate jokbo_page entries detected in chunk merge: [9, 11, 18, 24, 28, 33]
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Merged 3 chunks into 28 pages
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Duplicate jokbo_page entries detected in chunk merge: [25, 37, 40]
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Merged 2 chunks into 11 pages
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Duplicate jokbo_page entries detected in chunk merge: [3, 4, 7, 14, 33]
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Merged 4 chunks into 15 pages
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Merged 1 chunks into 5 pages
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Duplicate jokbo_page entries detected in chunk merge: [3, 16]
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Merged 2 chunks into 13 pages
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Duplicate jokbo_page entries detected in chunk merge: [10, 27, 31, 32]
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Merged 2 chunks into 9 pages
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Merged 1 chunks into 6 pages
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Merged 2 chunks into 2 pages
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Merged 2 chunks into 2 pages
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Duplicate jokbo_page entries detected in chunk merge: [29]
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Merged 2 chunks into 17 pages
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Merged 1 chunks into 2 pages
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Duplicate jokbo_page entries detected in chunk merge: [10, 29]
2025-09-08 17:22:34 - pdf_processor.parsers.result_merger - INFO - Merged 2 chunks into 6 pages
2025-09-08 17:22:34 - pdf_processor.core.processor - INFO - API Status: 5/5 available
2025-09-08 17:22:34 - pdf_processor.analyzers.jokbo_centric - INFO - Merged results: 37 pages, 75 questions
[2025-09-08 17:22:34,288: WARNING/ForkPoolWorker-2]   PDF 생성 시작: 37개 페이지, 75개 문제
[2025-09-08 17:22:34,291: WARNING/ForkPoolWorker-2]   문제 번호 순서대로 정렬됨: ['5', '6', '7', '8', '10', '15', '16', '17', '18', '22']...
[2025-09-08 17:22:35,226: WARNING/ForkPoolWorker-2] DEBUG: Question 22 is last on page 9, questions: [21, 22]
[2025-09-08 17:22:35,381: WARNING/ForkPoolWorker-2] DEBUG: Question 25 is last on page 10, questions: [23, 24, 25]
[2025-09-08 17:22:35,433: WARNING/ForkPoolWorker-2] DEBUG: Question 27 is last on page 11, questions: [26, 27]
[2025-09-08 17:22:35,542: WARNING/ForkPoolWorker-2] DEBUG: Question 31 is last on page 13, questions: [30, 31]
[2025-09-08 17:22:35,769: WARNING/ForkPoolWorker-2] DEBUG: Question 41 is last on page 18, questions: [41]
[2025-09-08 17:22:36,541: WARNING/ForkPoolWorker-2] DEBUG: Question 78 is last on page 37, questions: [77, 78]
[2025-09-08 17:22:36,793: WARNING/ForkPoolWorker-2] DEBUG: Question 83 is last on page 40, questions: [82, 83]
[2025-09-08 17:22:36,878: INFO/ForkPoolWorker-2] run_jokbo_analysis: use_multi=True, API_KEYS_count=5
2025-09-08 17:22:39 - pdf_processor.pdf.operations - INFO - Split Copy of 0904-2.노인수술-강병주 교수님.pdf - 장유담.pdf into 2 chunks (33 pages total)
2025-09-08 17:22:39 - pdf_processor.pdf.operations - INFO - Split Copy of 0903_2,3교시_쇼크, 외과적 출혈과 수혈_임경훈 교수님 - 박준영.pdf into 3 chunks (61 pages total)
2025-09-08 17:22:39 - pdf_processor.pdf.operations - INFO - Split Copy of 0902_1교시_외과 감염과 항생제 선택_김혜진 교수님 - 정연수.pdf into 2 chunks (37 pages total)
2025-09-08 17:22:39 - pdf_processor.pdf.operations - INFO - Split Copy of 0902-3.외과환자에서의 영양관리_이승수 교수님 - 장유담.pdf into 2 chunks (36 pages total)
2025-09-08 17:22:39 - pdf_processor.pdf.operations - INFO - Split Copy of 0901_1교시_수술 전 위험도 평가와 준비_송승호 교수님 - 박준영.pdf into 4 chunks (120 pages total)
2025-09-08 17:22:39 - pdf_processor.pdf.operations - INFO - Split Copy of 0829_7, 8교시_수술환자의 수분 전해질관리_황덕비 교수님_홍수현.pdf into 4 chunks (100 pages total)
2025-09-08 17:22:39 - pdf_processor.pdf.operations - INFO - Split Copy of 0829_6교시_수술합병증_권형준 교수님_박준영.pdf into 3 chunks (90 pages total)
2025-09-08 17:22:39 - pdf_processor.pdf.operations - INFO - Split Copy of 0829_1교시_외과학 개론_김형기 교수님 - 장유담.pdf into 2 chunks (53 pages total)
2025-09-08 17:22:39 - pdf_processor.pdf.operations - INFO - Split Copy of 0829_3교시_손상에의한전신반응_조성훈 교수님_정연수.pdf into 4 chunks (94 pages total)
2025-09-08 17:22:39 - pdf_processor.pdf.operations - INFO - Split Copy of 0904_3교시_흡입마취_김현지 교수님 - 정연수.pdf into 2 chunks (39 pages total)
2025-09-08 17:22:39 - pdf_processor.pdf.operations - INFO - Split Copy of 0903_4교시_유병혁교수님_외과 중환자 관리_ - 박민호.pdf into 2 chunks (60 pages total)
2025-09-08 17:22:39 - pdf_processor.pdf.operations - INFO - Split Copy of 0904_4교시_정맥마취_박성식 교수님 - 정연수.pdf into 2 chunks (45 pages total)
2025-09-08 17:22:39 - pdf_processor.pdf.operations - INFO - Split Copy of 0905_2교시_마취전 환자평가_관리_이정은 교수님_박민호.pdf into 2 chunks (43 pages total)
2025-09-08 17:22:39 - pdf_processor.pdf.operations - INFO - Split Copy of 0905_6,7,8교시_수술 전중후 환자 관리_이수현,이정은 교수님 - 이성현.pdf into 2 chunks (35 pages total)
2025-09-08 17:22:39 - pdf_processor.pdf.operations - INFO - Split Copy of 0908_6,7,8교시_CP수술전중후 환자관리2_임정아 교수님 - 박준영.pdf into 2 chunks (47 pages total)
2025-09-08 17:22:39 - pdf_processor.pdf.operations - INFO - Split Copy of 0908-1.환자감시장치_변성혜 교수님 - 장유담.pdf into 2 chunks (46 pages total)
2025-09-08 17:22:39 - pdf_processor.core.processor - INFO - Initialized PDFProcessor with session ID: c9898055-47b7-41d5-b933-7e3b7feb9ba3
2025-09-08 17:22:39 - pdf_processor.core.processor - INFO - Starting multi-API jokbo-centric analysis with 5 keys
2025-09-08 17:22:40 - pdf_processor.api.multi_api_manager - INFO - Initialized MultiAPIManager with 5 API keys: k0:***ZqkU, k1:***ZQkw, k2:***HJNw, k3:***_8Bo, k4:***nBNs
2025-09-08 17:22:40 - pdf_processor.pdf.operations - INFO - Split Copy of 0904-2.노인수술-강병주 교수님.pdf - 장유담.pdf into 2 chunks (33 pages total)
2025-09-08 17:22:40 - pdf_processor.core.processor - INFO - Lesson Copy of 0904-2.노인수술-강병주 교수님.pdf - 장유담.pdf will be processed in 2 chunks
2025-09-08 17:22:40 - pdf_processor.pdf.operations - INFO - Split Copy of 0903_2,3교시_쇼크, 외과적 출혈과 수혈_임경훈 교수님 - 박준영.pdf into 3 chunks (61 pages total)
2025-09-08 17:22:40 - pdf_processor.core.processor - INFO - Lesson Copy of 0903_2,3교시_쇼크, 외과적 출혈과 수혈_임경훈 교수님 - 박준영.pdf will be processed in 3 chunks
2025-09-08 17:22:40 - pdf_processor.pdf.operations - INFO - Split Copy of 0902_1교시_외과 감염과 항생제 선택_김혜진 교수님 - 정연수.pdf into 2 chunks (37 pages total)
2025-09-08 17:22:40 - pdf_processor.core.processor - INFO - Lesson Copy of 0902_1교시_외과 감염과 항생제 선택_김혜진 교수님 - 정연수.pdf will be processed in 2 chunks
2025-09-08 17:22:40 - pdf_processor.pdf.operations - INFO - Split Copy of 0902-3.외과환자에서의 영양관리_이승수 교수님 - 장유담.pdf into 2 chunks (36 pages total)
2025-09-08 17:22:40 - pdf_processor.core.processor - INFO - Lesson Copy of 0902-3.외과환자에서의 영양관리_이승수 교수님 - 장유담.pdf will be processed in 2 chunks
2025-09-08 17:22:40 - pdf_processor.pdf.operations - INFO - Split Copy of 0901_1교시_수술 전 위험도 평가와 준비_송승호 교수님 - 박준영.pdf into 4 chunks (120 pages total)
2025-09-08 17:22:40 - pdf_processor.core.processor - INFO - Lesson Copy of 0901_1교시_수술 전 위험도 평가와 준비_송승호 교수님 - 박준영.pdf will be processed in 4 chunks
2025-09-08 17:22:40 - pdf_processor.pdf.operations - INFO - Split Copy of 0829_7, 8교시_수술환자의 수분 전해질관리_황덕비 교수님_홍수현.pdf into 4 chunks (100 pages total)
2025-09-08 17:22:40 - pdf_processor.core.processor - INFO - Lesson Copy of 0829_7, 8교시_수술환자의 수분 전해질관리_황덕비 교수님_홍수현.pdf will be processed in 4 chunks
2025-09-08 17:22:40 - pdf_processor.pdf.operations - INFO - Split Copy of 0829_6교시_수술합병증_권형준 교수님_박준영.pdf into 3 chunks (90 pages total)
2025-09-08 17:22:40 - pdf_processor.core.processor - INFO - Lesson Copy of 0829_6교시_수술합병증_권형준 교수님_박준영.pdf will be processed in 3 chunks
2025-09-08 17:22:40 - pdf_processor.pdf.operations - INFO - Split Copy of 0829_1교시_외과학 개론_김형기 교수님 - 장유담.pdf into 2 chunks (53 pages total)
2025-09-08 17:22:40 - pdf_processor.core.processor - INFO - Lesson Copy of 0829_1교시_외과학 개론_김형기 교수님 - 장유담.pdf will be processed in 2 chunks
2025-09-08 17:22:41 - pdf_processor.pdf.operations - INFO - Split Copy of 0829_3교시_손상에의한전신반응_조성훈 교수님_정연수.pdf into 4 chunks (94 pages total)
2025-09-08 17:22:41 - pdf_processor.core.processor - INFO - Lesson Copy of 0829_3교시_손상에의한전신반응_조성훈 교수님_정연수.pdf will be processed in 4 chunks
2025-09-08 17:22:41 - pdf_processor.pdf.operations - INFO - Split Copy of 0904_3교시_흡입마취_김현지 교수님 - 정연수.pdf into 2 chunks (39 pages total)
2025-09-08 17:22:41 - pdf_processor.core.processor - INFO - Lesson Copy of 0904_3교시_흡입마취_김현지 교수님 - 정연수.pdf will be processed in 2 chunks
2025-09-08 17:22:41 - pdf_processor.pdf.operations - INFO - Split Copy of 0903_4교시_유병혁교수님_외과 중환자 관리_ - 박민호.pdf into 2 chunks (60 pages total)
2025-09-08 17:22:41 - pdf_processor.core.processor - INFO - Lesson Copy of 0903_4교시_유병혁교수님_외과 중환자 관리_ - 박민호.pdf will be processed in 2 chunks
2025-09-08 17:22:41 - pdf_processor.pdf.operations - INFO - Split Copy of 0904_4교시_정맥마취_박성식 교수님 - 정연수.pdf into 2 chunks (45 pages total)
2025-09-08 17:22:41 - pdf_processor.core.processor - INFO - Lesson Copy of 0904_4교시_정맥마취_박성식 교수님 - 정연수.pdf will be processed in 2 chunks
2025-09-08 17:22:41 - pdf_processor.pdf.operations - INFO - Split Copy of 0905_2교시_마취전 환자평가_관리_이정은 교수님_박민호.pdf into 2 chunks (43 pages total)
2025-09-08 17:22:41 - pdf_processor.core.processor - INFO - Lesson Copy of 0905_2교시_마취전 환자평가_관리_이정은 교수님_박민호.pdf will be processed in 2 chunks
2025-09-08 17:22:42 - pdf_processor.pdf.operations - INFO - Split Copy of 0905_6,7,8교시_수술 전중후 환자 관리_이수현,이정은 교수님 - 이성현.pdf into 2 chunks (35 pages total)
2025-09-08 17:22:42 - pdf_processor.core.processor - INFO - Lesson Copy of 0905_6,7,8교시_수술 전중후 환자 관리_이수현,이정은 교수님 - 이성현.pdf will be processed in 2 chunks
2025-09-08 17:22:42 - pdf_processor.pdf.operations - INFO - Split Copy of 0908_6,7,8교시_CP수술전중후 환자관리2_임정아 교수님 - 박준영.pdf into 2 chunks (47 pages total)
2025-09-08 17:22:42 - pdf_processor.core.processor - INFO - Lesson Copy of 0908_6,7,8교시_CP수술전중후 환자관리2_임정아 교수님 - 박준영.pdf will be processed in 2 chunks
2025-09-08 17:22:42 - pdf_processor.pdf.operations - INFO - Split Copy of 0908-1.환자감시장치_변성혜 교수님 - 장유담.pdf into 2 chunks (46 pages total)
2025-09-08 17:22:42 - pdf_processor.core.processor - INFO - Lesson Copy of 0908-1.환자감시장치_변성혜 교수님 - 장유담.pdf will be processed in 2 chunks
2025-09-08 17:22:42 - pdf_processor.api.multi_api_manager - INFO - Attempting operation with API key 0 (***ZqkU)
2025-09-08 17:22:42 - pdf_processor.api.multi_api_manager - INFO - Attempting operation with API key 1 (***ZQkw)
2025-09-08 17:22:42 - pdf_processor.analyzers.jokbo_centric - INFO - Analyzing lesson 'tmpd0oaopla.pdf' with jokbo 'Copy of 1728004686_193__230911_본1_치료의_기본_정답.pdf'
2025-09-08 17:22:42 - pdf_processor.analyzers.jokbo_centric - INFO - Analyzing lesson 'tmplklmxmo0.pdf' with jokbo 'Copy of 1728004686_193__230911_본1_치료의_기본_정답.pdf'
2025-09-08 17:22:42 - pdf_processor.api.multi_api_manager - INFO - Attempting operation with API key 2 (***HJNw)
2025-09-08 17:22:42 - pdf_processor.analyzers.jokbo_centric - INFO - Analyzing lesson 'tmpruotvt_j.pdf' with jokbo 'Copy of 1728004686_193__230911_본1_치료의_기본_정답.pdf'
2025-09-08 17:22:42 - pdf_processor.api.multi_api_manager - INFO - Attempting operation with API key 3 (***_8Bo)
2025-09-08 17:22:42 - pdf_processor.analyzers.jokbo_centric - INFO - Analyzing lesson 'tmpbss_lnc_.pdf' with jokbo 'Copy of 1728004686_193__230911_본1_치료의_기본_정답.pdf'
2025-09-08 17:22:42 - pdf_processor.api.multi_api_manager - INFO - Attempting operation with API key 4 (***nBNs)
2025-09-08 17:22:42 - pdf_processor.api.client - INFO - Uploading file: 족보_Copy of 1728004686_193__230911_본1_치료의_기본_정답.pdf [key=k2:***ZQkw]
2025-09-08 17:22:42 - pdf_processor.analyzers.jokbo_centric - INFO - Analyzing lesson 'tmp6a1rc4r2.pdf' with jokbo 'Copy of 1728004686_193__230911_본1_치료의_기본_정답.pdf'
2025-09-08 17:22:42 - pdf_processor.api.client - INFO - Uploading file: 족보_Copy of 1728004686_193__230911_본1_치료의_기본_정답.pdf [key=k1:***ZqkU]
2025-09-08 17:22:42 - pdf_processor.api.client - INFO - Uploading file: 족보_Copy of 1728004686_193__230911_본1_치료의_기본_정답.pdf [key=k4:***_8Bo]
2025-09-08 17:22:42 - pdf_processor.api.client - INFO - Uploading file: 족보_Copy of 1728004686_193__230911_본1_치료의_기본_정답.pdf [key=k3:***HJNw]
2025-09-08 17:22:42 - pdf_processor.api.client - INFO - Uploading file: 족보_Copy of 1728004686_193__230911_본1_치료의_기본_정답.pdf [key=k5:***nBNs]
[2025-09-08 17:22:42,981: INFO/ForkPoolWorker-2] HTTP Request: POST https://generativelanguage.googleapis.com/upload/v1beta/files "HTTP/1.1 200 OK"
[2025-09-08 17:22:42,984: INFO/ForkPoolWorker-2] HTTP Request: POST https://generativelanguage.googleapis.com/upload/v1beta/files "HTTP/1.1 200 OK"
[2025-09-08 17:22:43,013: INFO/ForkPoolWorker-2] HTTP Request: POST https://generativelanguage.googleapis.com/upload/v1beta/files "HTTP/1.1 200 OK"
[2025-09-08 17:22:43,063: INFO/ForkPoolWorker-2] HTTP Request: POST https://generativelanguage.googleapis.com/upload/v1beta/files "HTTP/1.1 200 OK"
[2025-09-08 17:22:43,078: INFO/ForkPoolWorker-2] HTTP Request: POST https://generativelanguage.googleapis.com/upload/v1beta/files "HTTP/1.1 200 OK"
[2025-09-08 17:22:43,643: INFO/ForkPoolWorker-2] HTTP Request: POST https://generativelanguage.googleapis.com/upload/v1beta/files?upload_id=ABgVH8-jaGRL7QPdLoGCFPWs1W8g1Jj6u1-DITi7mXDGjbiUs2nmRO9LZQMK9SRaFPVw3ZDhDJ3QdB7KMgj6QWdXCY7srLRHw3yQDjFr63SL1hY&upload_protocol=resumable "HTTP/1.1 200 OK"
2025-09-08 17:22:43 - pdf_processor.api.client - INFO - Successfully uploaded: 족보_Copy of 1728004686_193__230911_본1_치료의_기본_정답.pdf [key=k1:***ZqkU]
2025-09-08 17:22:43 - pdf_processor.api.client - INFO - Uploading file: 강의자료_tmplklmxmo0.pdf [key=k1:***ZqkU]
[2025-09-08 17:22:43,822: INFO/ForkPoolWorker-2] HTTP Request: POST https://generativelanguage.googleapis.com/upload/v1beta/files?upload_id=ABgVH8-trGkLvJSsEslOeXSLFVsihn2tx3O5Ce81re8l79h-O9d9QdWXj2ZhAWDE4kv89ubS9LijUS1dd4iO1jytTbvmPLcrTK2X804n1DBmSQ&upload_protocol=resumable "HTTP/1.1 200 OK"
2025-09-08 17:22:43 - pdf_processor.api.client - INFO - Successfully uploaded: 족보_Copy of 1728004686_193__230911_본1_치료의_기본_정답.pdf [key=k2:***ZQkw]
2025-09-08 17:22:43 - pdf_processor.api.client - INFO - Uploading file: 강의자료_tmpd0oaopla.pdf [key=k2:***ZQkw]
[2025-09-08 17:22:43,827: INFO/ForkPoolWorker-2] HTTP Request: POST https://generativelanguage.googleapis.com/upload/v1beta/files?upload_id=ABgVH8_xpQOzLvQvBIbNewZXERhnHe-5QScuzghMkWSB567q8EsP2JV0ASCLwAp6_XqQ47dSTaWBaQwht1oWPnS2YVuCD-dFLCwYHx5WFMrwb5Y&upload_protocol=resumable "HTTP/1.1 200 OK"
2025-09-08 17:22:43 - pdf_processor.api.client - INFO - Successfully uploaded: 족보_Copy of 1728004686_193__230911_본1_치료의_기본_정답.pdf [key=k4:***_8Bo]
2025-09-08 17:22:43 - pdf_processor.api.client - INFO - Uploading file: 강의자료_tmpbss_lnc_.pdf [key=k4:***_8Bo]
[2025-09-08 17:22:43,877: INFO/ForkPoolWorker-2] HTTP Request: POST https://generativelanguage.googleapis.com/upload/v1beta/files?upload_id=ABgVH88EE3b2eo92-5FMJp5nvgIWDoPIeSn5g_0aCNaxPgu6SW2Z1L3o3m5VFy4Mz94BGBXWP-dvKqwxkyZx9wBHmgVc96H5t8mQUpthdykqjw&upload_protocol=resumable "HTTP/1.1 200 OK"
2025-09-08 17:22:43 - pdf_processor.api.client - INFO - Successfully uploaded: 족보_Copy of 1728004686_193__230911_본1_치료의_기본_정답.pdf [key=k5:***nBNs]
2025-09-08 17:22:43 - pdf_processor.api.client - INFO - Uploading file: 강의자료_tmp6a1rc4r2.pdf [key=k5:***nBNs]
[2025-09-08 17:22:43,888: INFO/ForkPoolWorker-2] HTTP Request: POST https://generativelanguage.googleapis.com/upload/v1beta/files?upload_id=ABgVH8-iaeU3uk5ouPG8FuUSSAHkzwDN1j6SU51cb95tlJBUh50XaA2VHZoublYA-gU9C35zdCpcpMb52cV-Bi9pvfwlbZIyGvPSfEpEq9aTkcg&upload_protocol=resumable "HTTP/1.1 200 OK"
2025-09-08 17:22:43 - pdf_processor.api.client - INFO - Successfully uploaded: 족보_Copy of 1728004686_193__230911_본1_치료의_기본_정답.pdf [key=k3:***HJNw]
2025-09-08 17:22:43 - pdf_processor.api.client - INFO - Uploading file: 강의자료_tmpruotvt_j.pdf [key=k3:***HJNw]
[2025-09-08 17:22:43,908: INFO/ForkPoolWorker-2] HTTP Request: POST https://generativelanguage.googleapis.com/upload/v1beta/files "HTTP/1.1 200 OK"
[2025-09-08 17:22:44,002: INFO/ForkPoolWorker-2] HTTP Request: POST https://generativelanguage.googleapis.com/upload/v1beta/files "HTTP/1.1 200 OK"
[2025-09-08 17:22:44,083: INFO/ForkPoolWorker-2] HTTP Request: POST https://generativelanguage.googleapis.com/upload/v1beta/files "HTTP/1.1 200 OK"
[2025-09-08 17:22:44,161: INFO/ForkPoolWorker-2] HTTP Request: POST https://generativelanguage.googleapis.com/upload/v1beta/files "HTTP/1.1 200 OK"
[2025-09-08 17:22:44,240: INFO/ForkPoolWorker-2] HTTP Request: POST https://generativelanguage.googleapis.com/upload/v1beta/files "HTTP/1.1 200 OK"
[2025-09-08 17:22:44,586: INFO/ForkPoolWorker-2] HTTP Request: POST https://generativelanguage.googleapis.com/upload/v1beta/files?upload_id=ABgVH89b9j9GfkRlNjwJVw2otv9AESG8qvdCC2uQcIJ4kNnRiFfbpfKu001u5U0X8Q08nl-ldA-KYeGUdS6wiG9VK4yBSFnYPnOelOClfdBSoQ&upload_protocol=resumable "HTTP/1.1 200 OK"