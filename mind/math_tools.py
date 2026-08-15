from __future__ import annotations

import ast
import operator
import re
from decimal import Decimal, InvalidOperation


class MathInputError(ValueError):
    pass


_NUMBER = re.compile(
    r"(?<![\w.])(?P<open>\()?\s*(?P<sign>[+-])?\s*"
    r"(?P<number>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(?P<close>\))?(?![\w.])"
)
_NUMBERED_LIST_PREFIX = re.compile(r"^\s*\d+\s*[.)]\s+")


def normalize_math_text(text: str) -> str:
    return (
        text.replace("×", "*")
        .replace("✕", "*")
        .replace("·", "*")
        .replace("÷", "/")
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("＝", "=")
    )


def extract_numbers(text: str) -> list[Decimal]:
    values: list[Decimal] = []
    for raw_line in normalize_math_text(text).splitlines():
        # OCR often keeps numbered-list prefixes ("1. 250"). They are labels, not values.
        line = _NUMBERED_LIST_PREFIX.sub("", raw_line)
        for match in _NUMBER.finditer(line):
            token = match.group("number").replace(",", "")
            try:
                value = Decimal(token)
            except InvalidOperation:
                continue
            if match.group("sign") == "-" or (match.group("open") and match.group("close")):
                value = -value
            values.append(value)
    return values


def sum_number_list(text: str) -> str:
    values = extract_numbers(text)
    if not values:
        raise MathInputError("No numbers were found in the image.")
    total = sum(values, Decimal(0))
    formatted_total = format_decimal(total)
    if len(values) <= 12:
        expression = " + ".join(format_decimal(value) for value in values)
        expression = expression.replace("+ -", "− ")
        return f"{expression} = {formatted_total}"
    return f"Total ({len(values)} numbers): {formatted_total}"


def format_decimal(value: Decimal) -> str:
    if value == value.to_integral():
        return f"{int(value):,}"
    plain = format(value.normalize(), "f")
    integer, decimal = plain.split(".", 1)
    sign = ""
    if integer.startswith("-"):
        sign, integer = "-", integer[1:]
    return f"{sign}{int(integer):,}.{decimal.rstrip('0')}"


_SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_MATH_SYMBOLS = set("+-*/=^√π∑∫≤≥≠<>±×÷·≈%")
_MATH_KEYWORDS = re.compile(
    r"\b(?:"
    r"calculate|compute|solve|evaluate|simplify|factor|integrate|differentiate|"
    r"derivative|integral|equation|formula|matrix|vector|polynomial|logarithm|"
    r"percentage|percent|probability|ratio|fraction|perimeter|area|volume|hypotenuse|"
    r"speed|velocity|distance|acceleration|cost|price|discount|interest|profit|loss|"
    r"how\s+(?:many|much|long|far|fast|old)|"
    r"(?:what|find|determine)\s+(?:is\s+)?(?:the\s+)?(?:value|total|sum|average|root|speed|distance|cost|price|rate|time|amount|fraction|ratio|answer|result|solution)|"
    r"find\s+[xyzabckmn]|sum\s+of|product\s+of|average\s+of|"
    r"sqrt|sin|cos|tan|log|ln"
    r")\b",
    re.IGNORECASE,
)
_WORD_PROBLEM_INDICATORS = re.compile(
    r"\b(?:"
    r"travels|traveling|moves|moving|drives|driving|speeds|flying|running|walks|"
    r"bought|buys|purchased|sold|sells|spends|spent|cost|costs|earned|earns|paid|"
    r"miles|kilometers|km|meters|hours|minutes|seconds|mph|km/h|liters|gallons|grams|kg|dollars"
    r")\b",
    re.IGNORECASE,
)
_EQUATION_PATTERN = re.compile(r"[a-zA-Z0-9_().+\-*/^\s]+=[a-zA-Z0-9_().+\-*/^\s]+")


def _eval_ast_node(node: ast.AST) -> float | int | None:
    if isinstance(node, ast.Expression):
        return _eval_ast_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp):
        left = _eval_ast_node(node.left)
        right = _eval_ast_node(node.right)
        if left is None or right is None:
            return None
        op_type = type(node.op)
        if op_type in _SAFE_OPERATORS:
            if op_type in (ast.Div, ast.FloorDiv, ast.Mod) and right == 0:
                return None
            if op_type == ast.Pow and (abs(right) > 100 or abs(left) > 10000):
                return None
            return _SAFE_OPERATORS[op_type](left, right)
    if isinstance(node, ast.UnaryOp):
        operand = _eval_ast_node(node.operand)
        if operand is None:
            return None
        op_type = type(node.op)
        if op_type in _SAFE_OPERATORS:
            return _SAFE_OPERATORS[op_type](operand)
    return None


def solve_math_locally(text: str) -> str | None:
    """Attempt to safely evaluate a basic arithmetic expression locally."""
    if not text:
        return None
    cleaned = normalize_math_text(text).replace("^", "**").replace(",", "").strip()
    cleaned = cleaned.strip("\"'`“”‘’`")
    cleaned = cleaned.rstrip(" =?").strip()
    cleaned = cleaned.strip("\"'`“”‘’`")

    # Percentage expressions like "15% of 80" or "20% * 50"
    pct_match = re.match(r"^([\d.]+)\s*%\s*(?:of|\*)\s*([\d.]+)$", cleaned, re.IGNORECASE)
    if pct_match:
        try:
            val = (float(pct_match.group(1)) / 100.0) * float(pct_match.group(2))
            if abs(val) == int(abs(val)):
                return f"{int(val):,}"
            return f"{val:g}"
        except Exception:
            pass

    # Must contain at least one arithmetic operator
    if not any(op in cleaned for op in ("+", "-", "*", "/", "%", "**")):
        return None

    try:
        tree = ast.parse(cleaned, mode="eval")
        val = _eval_ast_node(tree)
        if val is not None and isinstance(val, (int, float)):
            if abs(val) == int(abs(val)):
                return f"{int(val):,}"
            return f"{val:g}"
    except Exception:
        return None
    return None


def is_math_or_number_problem(text: str) -> bool:
    """Return True if the text represents a math equation or numeric problem."""
    if not text:
        return False
    cleaned = text.strip().strip("\"'`“”‘’`").strip()
    if len(cleaned) < 2:
        return False

    has_math_symbol = any(ch in _MATH_SYMBOLS for ch in cleaned)
    digits = re.findall(r"\b\d+(?:\.\d+)?\b", cleaned)
    has_digit = len(digits) > 0

    # 1. Equation with variables or values
    if "=" in cleaned and _EQUATION_PATTERN.search(cleaned):
        return True

    # 2. Mathematical expression with digits and operations
    if has_digit and has_math_symbol:
        return True

    # 3. Numeric word problem with math keywords
    if has_digit and _MATH_KEYWORDS.search(cleaned):
        return True

    # 4. Word problem with numbers and context indicators (e.g. 60 miles in 2 hours)
    if has_digit and _WORD_PROBLEM_INDICATORS.search(cleaned) and (
        len(digits) >= 2 or any(w in cleaned.lower() for w in ("what", "how", "find", "total", "if", "when"))
    ):
        return True

    # 5. LaTeX math commands
    if any(cmd in cleaned for cmd in ("\\frac", "\\sqrt", "\\int", "\\sum", "\\cdot")):
        return True

    return False

