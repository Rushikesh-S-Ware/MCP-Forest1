"""
Input validators and guard rails for MCP.
"""
import re
from typing import Any, Dict
import logging

logger = logging.getLogger(__name__)

class InputValidator:
    """Validate and sanitize MCP inputs."""
    
    # Allowed ranges
    MIN_YEAR = 2001
    MAX_YEAR = 2024
    ALLOWED_THRESHOLDS = [0, 10, 15, 20, 25, 30, 50, 75]
    
    # Dangerous patterns
    SQL_INJECTION_PATTERNS = [
        r";\s*(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE|EXEC)",
        r"UNION\s+SELECT",
        r"--",
        r"/\*.*\*/",
        r"xp_cmdshell",
    ]
    
    @classmethod
    def validate_year(cls, year: Any) -> int:
        """Validate year input."""
        try:
            year_int = int(year)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid year: {year}")
        
        if year_int < cls.MIN_YEAR or year_int > cls.MAX_YEAR:
            raise ValueError(
                f"Year {year_int} out of range ({cls.MIN_YEAR}-{cls.MAX_YEAR})"
            )
        
        return year_int
    
    @classmethod
    def validate_threshold(cls, threshold: Any) -> int:
        """Validate threshold input."""
        try:
            threshold_int = int(threshold)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid threshold: {threshold}")
        
        if threshold_int not in cls.ALLOWED_THRESHOLDS:
            raise ValueError(
                f"Threshold {threshold_int} not allowed. "
                f"Must be one of: {cls.ALLOWED_THRESHOLDS}"
            )
        
        return threshold_int
    
    @classmethod
    def detect_sql_injection(cls, text: str) -> bool:
        """Detect potential SQL injection attempts."""
        text_upper = text.upper()
        
        for pattern in cls.SQL_INJECTION_PATTERNS:
            if re.search(pattern, text_upper, re.IGNORECASE):
                logger.warning(f"SQL injection attempt detected: {text}")
                return True
        
        return False
    
    @classmethod
    def sanitize_input(cls, text: str) -> str:
        """Sanitize text input."""
        # Remove control characters
        sanitized = ''.join(char for char in text if ord(char) >= 32 or char in '\n\r\t')
        
        # Limit length
        max_length = 1000
        if len(sanitized) > max_length:
            logger.warning(f"Input truncated from {len(sanitized)} to {max_length} chars")
            sanitized = sanitized[:max_length]
        
        return sanitized
    
    @classmethod
    def validate_query(cls, question: str, entities: Dict) -> Dict:
        """Validate complete query."""
        errors = []
        
        # Check for SQL injection
        if cls.detect_sql_injection(question):
            errors.append("Potential SQL injection detected")
        
        # Validate year if present
        if "year" in entities:
            try:
                entities["year"] = cls.validate_year(entities["year"])
            except ValueError as e:
                errors.append(str(e))
        
        # Validate threshold if present
        if "threshold" in entities:
            try:
                entities["threshold"] = cls.validate_threshold(entities["threshold"])
            except ValueError as e:
                errors.append(str(e))
        
        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "entities": entities
        }