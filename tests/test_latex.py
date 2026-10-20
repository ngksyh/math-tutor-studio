from services.latex import has_unwrapped_math, normalize_math


def test_normalize_paren_and_bracket_delimiters():
    assert normalize_math(r"prove \(\frac{h}{2}<1\).") == r"prove $\frac{h}{2}<1$."
    assert normalize_math(r"so \[ x^2 \] holds") == r"so $$x^2$$ holds"


def test_matrix_line_break_spacing_is_untouched():
    text = r"$\begin{pmatrix}1\\[2pt]2\end{pmatrix}$"
    assert normalize_math(text) == text


def test_detects_unwrapped_math():
    assert has_unwrapped_math(r"Let A_t=\begin{pmatrix}3&t\\0&3\end{pmatrix}, t\in\mathbb{R}.")
    assert has_unwrapped_math(r"for x^2 large")
    assert not has_unwrapped_math(r"Let $A_t=\begin{pmatrix}3&t\\0&3\end{pmatrix}$ and $$\int_0^1 f$$.")
    assert not has_unwrapped_math(r"Prove \(\frac{h}{6}\) is positive.")  # fixed by normalize
    assert not has_unwrapped_math("Plain words, a price of 5 dollars, snake_case is fine.")
    assert not has_unwrapped_math(None)


def test_missing_values_are_safe():
    assert normalize_math(float("nan")) == "" and not has_unwrapped_math(float("nan"))
