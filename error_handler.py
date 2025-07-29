"""
Centralized error handling for the JokboDude project
"""
import traceback
from typing import Optional, Dict, Any
from pathlib import Path
import sys


class ErrorHandler:
    """Standardized error handling across the application"""
    
    @staticmethod
    def handle_file_error(operation: str, filepath: Path, error: Exception) -> None:
        """Handle file-related errors consistently
        
        Args:
            operation: Operation being performed (e.g., "read", "write", "delete")
            filepath: Path to the file
            error: The exception that occurred
        """
        if isinstance(error, FileNotFoundError):
            print(f"  오류: {operation} 실패 - 파일을 찾을 수 없습니다: {filepath}")
        elif isinstance(error, PermissionError):
            print(f"  오류: {operation} 실패 - 권한이 없습니다: {filepath}")
        elif isinstance(error, (OSError, IOError)):
            print(f"  오류: {operation} 실패 - 파일 시스템 오류: {filepath}")
            print(f"    세부사항: {str(error)}")
        else:
            print(f"  오류: {operation} 실패 - 예상치 못한 오류: {filepath}")
            print(f"    세부사항: {str(error)}")
    
    @staticmethod
    def handle_api_error(api_name: str, error: Exception) -> Dict[str, Any]:
        """Handle API-related errors consistently
        
        Args:
            api_name: Name of the API (e.g., "Gemini")
            error: The exception that occurred
            
        Returns:
            Error dictionary with consistent format
        """
        error_msg = str(error)
        
        if "quota" in error_msg.lower() or "rate limit" in error_msg.lower():
            print(f"  오류: {api_name} API 할당량 초과 - 잠시 후 다시 시도하세요")
            return {"error": "API quota exceeded", "retry_after": 60}
        elif "timeout" in error_msg.lower():
            print(f"  오류: {api_name} API 시간 초과 - 네트워크 연결을 확인하세요")
            return {"error": "API timeout", "retry": True}
        elif "authentication" in error_msg.lower() or "api key" in error_msg.lower():
            print(f"  오류: {api_name} API 인증 실패 - API 키를 확인하세요")
            return {"error": "Authentication failed"}
        else:
            print(f"  오류: {api_name} API 호출 실패")
            print(f"    세부사항: {error_msg}")
            return {"error": f"{api_name} API error", "details": error_msg}
    
    @staticmethod
    def handle_pdf_error(operation: str, pdf_path: str, error: Exception) -> None:
        """Handle PDF-specific errors
        
        Args:
            operation: Operation being performed
            pdf_path: Path to the PDF file
            error: The exception that occurred
        """
        error_msg = str(error).lower()
        
        if "encrypted" in error_msg or "password" in error_msg:
            print(f"  오류: {operation} 실패 - PDF가 암호화되어 있습니다: {pdf_path}")
        elif "corrupt" in error_msg or "invalid" in error_msg:
            print(f"  오류: {operation} 실패 - PDF 파일이 손상되었습니다: {pdf_path}")
        elif "page" in error_msg and "not found" in error_msg:
            print(f"  오류: {operation} 실패 - 요청한 페이지가 존재하지 않습니다: {pdf_path}")
        else:
            ErrorHandler.handle_file_error(operation, Path(pdf_path), error)
    
    @staticmethod
    def log_exception(context: str, error: Exception, debug: bool = False) -> None:
        """Log exception with context
        
        Args:
            context: Context where the error occurred
            error: The exception that occurred
            debug: Whether to print full traceback
        """
        print(f"\n오류 발생: {context}")
        print(f"  유형: {type(error).__name__}")
        print(f"  메시지: {str(error)}")
        
        if debug:
            print("\n상세 추적:")
            traceback.print_exc()
    
    @staticmethod
    def create_error_response(error_type: str, message: str, **kwargs) -> Dict[str, Any]:
        """Create standardized error response
        
        Args:
            error_type: Type of error
            message: Error message
            **kwargs: Additional error details
            
        Returns:
            Standardized error dictionary
        """
        response = {
            "error": error_type,
            "message": message
        }
        response.update(kwargs)
        return response