"""A deliberately limited Harlowe interpreter for the downloaded stories.

No Python eval or JavaScript execution: unsupported syntax fails explicitly.
"""

import operator
import re


class UnsupportedTwineError(ValueError):
    """The passage needs syntax this console interpreter cannot execute."""


def balanced(text, start, opening, closing):
    depth = 0
    quote = None
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        # Quotes protect parentheses in macro arguments, not brackets in prose.
        if opening == "(":
            if quote:
                if char == quote and not escaped:
                    quote = None
                escaped = char == "\\" and not escaped
                continue
            if char in "\"'":
                quote = char
                continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return text[start + 1:index], index + 1
    raise UnsupportedTwineError(f"Unclosed {opening!r} at offset {start}")


class Renderer:
    macro = re.compile(r"\(([\w-]+):", re.UNICODE)
    browser_commands = {"save-game", "load-game", "reload", "goto-url"}
    styling = {"text-colour", "text-color", "text-style"}

    def __init__(self, variables, rng):
        self.variables = variables
        self.rng = rng
        self.notices = []

    def value(self, expression):
        expression = expression.strip()
        if expression in ("true", "false"):
            return expression == "true"
        if re.fullmatch(r"-?\d+", expression):
            return int(expression)
        if re.fullmatch(r"\$\w+", expression):
            return self.variables.get(expression[1:], 0)
        if re.fullmatch(r'''"[^"\\]*"|'[^'\\]*' ''', expression, re.VERBOSE):
            return expression[1:-1]
        random_match = re.fullmatch(r"\(random:\s*(-?\d+)\s*,\s*(-?\d+)\s*\)", expression)
        if random_match:
            low, high = sorted(map(int, random_match.groups()))
            return self.rng.randint(low, high)
        arithmetic = re.fullmatch(r"(\$\w+|-?\d+)\s*([+-])\s*(\d+)", expression)
        if arithmetic:
            left, operation, right = arithmetic.groups()
            value = self.value(left)
            if type(value) is not int:
                raise UnsupportedTwineError(f"Non-numeric arithmetic: {expression}")
            return (operator.add if operation == "+" else operator.sub)(value, int(right))
        raise UnsupportedTwineError(f"Unsupported value expression: {expression}")

    def condition(self, expression):
        match = re.fullmatch(r"\s*(\$\w+)\s*(is\s+not|is\s*[<>]=?|is|>=|<=|>|<)\s*(.+?)\s*", expression)
        if not match:
            raise UnsupportedTwineError(f"Unsupported condition: {expression}")
        left, comparison, right = match.groups()
        left, right = self.value(left), self.value(right)
        comparison = re.sub(r"\s+", " ", comparison).strip()
        if comparison in ("is", "is not"):
            equal = type(left) is type(right) and left == right
            return equal if comparison == "is" else not equal
        comparison = comparison.removeprefix("is").strip()
        if type(left) is not int or type(right) is not int:
            raise UnsupportedTwineError(f"Non-numeric comparison: {expression}")
        return {">": operator.gt, "<": operator.lt, ">=": operator.ge, "<=": operator.le}[comparison](left, right)

    def render(self, text):
        output = []
        index = 0
        # Conditional chains are local to each hook, never shared with a parent.
        chain_taken = None
        while index < len(text):
            if text.startswith("[[", index):
                end = text.find("]]", index + 2)
                if end == -1:
                    raise UnsupportedTwineError("Unclosed passage link")
                output.append(text[index:end + 2])
                index = end + 2
                chain_taken = None
                continue
            match = self.macro.match(text, index)
            if match:
                body, end = balanced(text, index, "(", ")")
                name, arguments = body.split(":", 1)
                name = name.lower()
                hook_start = end
                while hook_start < len(text) and text[hook_start].isspace():
                    hook_start += 1
                if name in {"if", "elseif", "else-if", "else"} and text.startswith("[[", hook_start) and not text.startswith("[[[", hook_start):
                    # In the bundled Harlowe runtime these changers do not hide
                    # a bare link. Preserve the authored behavior, and report it.
                    self.notices.append("Conditional on a bare passage link has no effect; use a surrounding hook")
                    index = end
                    chain_taken = None
                    continue
                if name in {"if", "elseif", "else-if", "else"} | self.styling | {"link"}:
                    if hook_start >= len(text) or text[hook_start] != "[":
                        raise UnsupportedTwineError(f"({name}:) requires a hook")
                    hook, index = balanced(text, hook_start, "[", "]")
                    if name == "if":
                        selected = self.condition(arguments)
                        chain_taken = selected
                    elif name in {"elseif", "else-if", "else"}:
                        if chain_taken is None:
                            raise UnsupportedTwineError(f"({name}:) has no preceding conditional hook")
                        selected = not chain_taken and (name == "else" or self.condition(arguments))
                        chain_taken = chain_taken or selected
                        if name == "else":
                            chain_taken = None
                    elif name == "link":
                        # Only browser-control hooks occur in these stories. Do not
                        # execute click effects merely by displaying a passage.
                        commands = set(re.findall(r"\(([\w-]+):", hook))
                        if not commands or not commands <= self.browser_commands:
                            raise UnsupportedTwineError("Interactive (link:) hooks are unsupported")
                        self.notices.append(f"Browser control unavailable: {arguments.strip()}")
                        selected = False
                        chain_taken = None
                    else:
                        selected = True
                        chain_taken = None
                    if selected:
                        output.append(self.render(hook))
                    continue
                index = end
                chain_taken = None
                if name == "set":
                    assignment = re.fullmatch(r"\s*\$(\w+)\s*(?:=|to\b)\s*(.+)\s*", arguments, re.DOTALL)
                    if not assignment:
                        raise UnsupportedTwineError(f"Unsupported assignment: {arguments}")
                    variable, expression = assignment.groups()
                    self.variables[variable] = self.value(expression)
                elif name in self.browser_commands:
                    self.notices.append(f"Browser command unavailable: ({name}:)")
                else:
                    raise UnsupportedTwineError(f"Unsupported macro: ({name}:)")
                continue
            if text[index] == "[":
                hook, index = balanced(text, index, "[", "]")
                output.append(self.render(hook))
                chain_taken = None
                continue
            variable = re.match(r"\$(\w+)", text[index:])
            if variable:
                value = self.variables.get(variable.group(1), 0)
                output.append(str(value).lower() if type(value) is bool else str(value))
                index += len(variable.group(0))
                chain_taken = None
                continue
            char = text[index]
            output.append(char)
            if not char.isspace():
                chain_taken = None
            index += 1
        return "".join(output)
