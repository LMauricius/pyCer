import regex as re
from dataclasses import dataclass, field
from typing import List, Union, Optional, Callable
from enum import Enum, auto

# ============================
# === Lexer Implementation ===
# ============================

UNICODE_IDENT = r"([\p{L}\p{M}\p{N}_]|'.')+"


# Types of tokens
class TokenType(Enum):
    COMMENT = auto()
    IDENT = auto()
    SYMBOL = auto()
    TEXTSYM = auto()
    LPAREN = auto()
    RPAREN = auto()
    PIPE = auto()
    EQUAL = auto()
    WS = auto()
    NEWLINE = auto()
    EOF = auto()


token_specification = [
    (TokenType.COMMENT, r"#[^\n]*"),
    (TokenType.IDENT, UNICODE_IDENT),
    (TokenType.SYMBOL, r"[^\s#()|\\'=]|\\" + UNICODE_IDENT),
    (TokenType.LPAREN, r"\("),
    (TokenType.RPAREN, r"\)"),
    (TokenType.PIPE, r"\|"),
    (TokenType.EQUAL, r"="),
    (TokenType.WS, r"[ \t]+"),
    (TokenType.NEWLINE, r"\n"),
]

# Compile with Unicode property support
combined_regex = "|".join(
    f"(?P<{tokenType.name}>{regex})" for tokenType, regex in token_specification
)
lexer = re.compile(combined_regex, re.UNICODE)


@dataclass
class Token:
    type: TokenType
    text: str
    line: int
    col: int
    ws_before: bool


def lex(code: str) -> List[Token]:
    tokens = []
    line, col, last_end = 1, 1, 0
    ws_before = True
    for match in lexer.finditer(code):
        kind = match.lastgroup
        value = match.group()
        start = match.start()
        last_end = match.end()

        if kind == TokenType.COMMENT.name:
            col += len(value)
        elif kind == TokenType.NEWLINE.name:
            line += 1
            col = 1
            tokens.append(Token(TokenType[kind], "a new line", line, col, ws_before))
            ws_before = True
        elif kind == TokenType.WS.name:
            col += len(value)
            ws_before = True
        else:
            tokens.append(Token(TokenType[kind], value, line, col, ws_before))
            col += len(value)
            ws_before = False

    tokens.append(Token(TokenType.EOF, "the end of file", line, col, ws_before))
    return tokens


# ============================
# === AST Node Definitions ===
# ============================


@dataclass
class Identifier:
    name: str


@dataclass
class Symbol:
    value: str


@dataclass
class Choice:
    items: List["Item"]


@dataclass
class Group:
    choices: List[Choice]


@dataclass
class FunctionCall:
    parts: List[Union[Symbol, "Item"]]


@dataclass
class Production:
    name: Union[Identifier, FunctionCall]
    rhs: Group


Item = Union[Identifier, Group, FunctionCall, Production]


# ============================
# === Parser Implementation ===
# ============================
@dataclass
class ParseError:
    pos: int
    message: str


class Parser:
    """
    Structural parser for MGF using the provided tokenizer output.
    Key rules implemented:
      - Productions: a line that contains a standalone '=' where the LHS starts at column 1
        and the full LHS (on that line) is either a single Identifier or a FunctionCall (no spaces splitting multiple items).
      - FunctionCall: a contiguous *run* of tokens (no whitespace between consecutive tokens)
        that contains at least one SYMBOL/TEXTSYM and at least one item (IDENT/QUOTED/Group).
      - Group: parentheses with nested parsing; supports '|' to form Choice.
      - Choice: one or more alternatives separated by '|', each alternative is a list of items.
    Assumptions:
      - Productions are single-line (RHS ends at end-of-line).
    """

    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.position = 0
        self.globalGroup = Group([])
        self.errors: List[ParseError] = []

    # -----------------------------
    # Public API
    # -----------------------------
    def parse(self) -> Group:
        return Group(
            self._parse_choice_list(TokenType.EOF, "the end of file", True, "MGF file")
        )

    def current(self) -> Token:
        return self.tokens[self.position]

    def _parse_choice_list(
        self,
        terminatorType: TokenType,
        terminator: str,
        multiline: bool,
        targetFriendlyName: str,
    ) -> List[Choice]:
        """
        Parse a list of choices, ended by a terminator.
        Returns the list
        Always consumes tokens until the terminator
        """
        choice = Choice([])
        choices: List[Choice] = [choice]

        startPos = self.position

        while not (
            self.current().type == terminatorType
            and (
                terminatorType == TokenType.EOF
                or terminatorType == TokenType.NEWLINE
                or self.current().text == terminator
            )
        ):
            if multiline:
                while self.current().type == TokenType.NEWLINE:
                    self.position += 1

            if self.current().type == TokenType.EOF:  # and it's not the terminator
                self.errors.append(
                    ParseError(
                        startPos,
                        f"File ended before closing {targetFriendlyName} with {terminator}",
                    )
                )
                return choices

            if self.current().type == TokenType.PIPE:
                self.position += 1
                choice = Choice([])
                choices.append(choice)
            else:
                item = self._parse_item()
                if item is not None:
                    choice.items.append(item)
                elif self.current().type != TokenType.EOF:
                    self.errors.append(
                        ParseError(
                            self.position,
                            f"Did not expect {self.current().text}",
                        )
                    )
                    self.position += 1

        return choices

    def _parse_item(self) -> Optional[Item]:
        """
        Parse any item, if possible
        Returns the item and consumes tokens if successful.
        Returns None if the current token is not a start of an item
        """
        if (item := self._try_parse_production()) is not None:
            return item
        elif (item := self._try_parse_identifier()) is not None:
            return item
        elif (item := self._try_parse_function_call()) is not None:
            return item
        elif (item := self._try_parse_group()) is not None:
            return item
        else:
            return None

    def _try_parse_identifier(self) -> Optional[Identifier]:
        """
        Parse an identifier item, if possible
        Returns the identifier and consumes a token if successful.
        Returns None if the current token is not an identifier
        """
        if self.current().type == TokenType.IDENT:
            # Remove quotes and add the identifier
            text = re.sub(r"'(.)'", r"\1", self.current().text)
            self.position += 1
            return Identifier(text)
        else:
            return None

    def _try_parse_function_call(self) -> Optional[FunctionCall]:
        pass

    def _try_parse_group(self) -> Optional[Group]:
        """
        Parse a group, if possible
        Returns the group and consumes tokens if successful.
        Returns None if the current token is not a start of a group
        """
        if self.current().type == TokenType.LPAREN:
            self.position += 1
            choices = self._parse_choice_list(TokenType.RPAREN, ")", True, "group")
            if self.current().type == TokenType.RPAREN:
                self.position += 1
            return Group(choices)
        else:
            return None

    def _try_parse_production(self) -> Optional[Production]:
        """
        Parse a production, if possible
        Returns the production and consumes tokens if successful.
        Returns None if the current token is not a start of a production
        """
        oldPosition = self.position
        oldErrorCount = len(self.errors)

        if (nameItem := self._try_parse_identifier()) is not None:
            pass
        elif (nameItem := self._try_parse_function_call()) is not None:
            pass
        else:
            return None

        if self.current().type == TokenType.EQUAL:
            self.position += 1
            production = Production(
                nameItem,
                Group(
                    self._parse_choice_list(
                        TokenType.NEWLINE, "a new line", False, "production"
                    )
                ),
            )
            if self.current().type == TokenType.NEWLINE:
                self.position += 1

            # Alternatives with '=' sign
            while self.current().type == TokenType.EQUAL:
                self.position += 1
                production.rhs.choices.extend(
                    self._parse_choice_list(TokenType.NEWLINE, "", False, "production")
                )
                if self.current().type == TokenType.NEWLINE:
                    self.position += 1
            return production
        else:
            self.position = oldPosition
            self.errors = self.errors[:oldErrorCount]
            return None


# ============================
# === Example Usage ===
# ============================

import pprint

# --- Example usage & tests ---
if __name__ == "__main__":
    sample = r"""
        # Example MGF input
        Digit = 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9
        \optional:Pattern = Pattern | 
            \repeat{1-5}Letter
        IdentifierWithQuotes = 'a''b'c' 'd'  # demonstrates quoted parts concatenated
        GroupExample = (Digit Letter Digit)
        Expr = Expression ('+' | '-') Number
        \thrice:Pattern = Pattern Pattern Pattern
        # end
    """

    tokens = lex(sample)

    print("TOKENS:")
    pprint.pprint(tokens)

    parser = Parser(tokens)
    ast = parser.parse()

    print("\nAST:")
    pprint.pprint(ast)
    for error in parser.errors:
        print(
            f"Error @{tokens[error.pos].line}:{tokens[error.pos].col}: {error.message}"
        )
