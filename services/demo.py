"""Sample content for demo mode (MTS_DEMO=1), so reviewers see the whole pipeline at work.

Everything here is written for the demo: the two research sources are labelled as fictional, and
the questions, the verified solution and both post drafts are original. Nothing is copied from Stack
Exchange or course material. Loaded once into data/demo.db, never into your real database.
"""
from __future__ import annotations

import json

from db.repository import Repository
from services.draft_generator import check_post

DEMO_BATCH = "batch_demo"


def _topic_ids(repo: Repository) -> dict[str, str]:
    return dict(zip(repo.topics_df()["name"], repo.topics_df()["topic_id"]))


QUESTIONS = [
    dict(key="mvt", topic="Mean Value Theorem", title="Sine is 1-Lipschitz",
         question_latex=r"Use the Mean Value Theorem to prove that $|\sin a-\sin b|\le|a-b|$ for all real $a, b$.",
         question_type="proof", difficulty=2, expected_time_min=8, target_audience="intro_proofs",
         proposed_solution=r"Assume $a<b$. By the MVT there is $c\in(a,b)$ with $\sin b-\sin a=\cos c\,(b-a)$; "
                           r"since $|\cos c|\le 1$, the inequality follows. The case $a=b$ is trivial.",
         teaching_insight="Teaching judgment: a bound on the derivative becomes a bound on differences of values.",
         status="selected"),
    dict(key="span", topic="Linear independence and span", title="A redundant vector",
         question_latex=r"Let $v_1=(1,2,3)$, $v_2=(2,4,6)$ and $v_3=(0,1,1)$ in $\mathbb{R}^3$. Is "
                        r"$\{v_1,v_2,v_3\}$ linearly independent? Find a basis of "
                        r"$\operatorname{span}\{v_1,v_2,v_3\}$ consisting of vectors from the set.",
         question_type="computational", difficulty=2, expected_time_min=6, target_audience="university_intro",
         proposed_solution=r"No: $v_2=2v_1$, so $2v_1-v_2+0v_3=0$ is a non-trivial relation. Removing $v_2$ "
                           r"does not change the span, and $v_1, v_3$ are independent (neither is a multiple "
                           r"of the other), so $\{v_1,v_3\}$ is a basis and the span has dimension $2$.",
         teaching_insight="Teaching judgment: a vector that is a combination of the others can be dropped "
                          "without changing the span.",
         status="candidate"),
    dict(key="eps", topic="Epsilon–delta definition of a limit", title="Choosing delta for x squared",
         question_latex=r"Prove directly from the $\varepsilon$-$\delta$ definition that $\lim_{x\to 2}x^2=4$.",
         question_type="proof", difficulty=3, expected_time_min=10, target_audience="intro_proofs",
         proposed_solution=r"Let $\varepsilon>0$ and set $\delta=\min\{1,\varepsilon/5\}$. If "
                           r"$0<|x-2|<\delta$ then $1<x<3$, so $|x+2|<5$ and "
                           r"$|x^2-4|=|x-2|\,|x+2|<5\delta\le\varepsilon$.",
         teaching_insight=r"Teaching judgment: $\delta$ may depend only on $\varepsilon$; the cap "
                          r"$\delta\le 1$ is what bounds the factor $|x+2|$.",
         status="favorite"),
]

SOLUTION = dict(
    raw_reasoning=(
        "If $a=b$ there is nothing to prove, so assume $a<b$ (the inequality is symmetric in $a$ and $b$).\n\n"
        "The function $f(x)=\\sin x$ is continuous on $[a,b]$ and differentiable on $(a,b)$, so the Mean "
        "Value Theorem gives some $c\\in(a,b)$ with\n$$\\sin b-\\sin a=f'(c)\\,(b-a)=\\cos c\\,(b-a).$$\n\n"
        "Since $|\\cos c|\\le 1$,\n$$|\\sin b-\\sin a|=|\\cos c|\\,|b-a|\\le|b-a|.$$"),
    intuition=("A bound on the derivative turns into a bound on differences of values: if $|f'|\\le M$ on an "
               "interval, then $|f(x)-f(y)|\\le M|x-y|$ there. We never need to know where $c$ is."),
    common_mistakes=("Skipping the hypotheses (continuity on $[a,b]$, differentiability on $(a,b)$); trying to "
                     "find $c$ explicitly; assuming $a<b$ without saying why that is enough."),
)

REVIEW = {
    "model": "demo", "reviewed_at": "2026-10-20T00:00:00", "verdict": "minor_issues",
    "summary": "Correct and complete: the hypotheses are checked and symmetry covers the case $a>b$.",
    "issues": [{"severity": "style", "location": "assume $a<b$",
                "description": "The symmetry remark carries the case $a>b$; it is easy to miss in brackets.",
                "suggestion": "Give it its own sentence after the main inequality."}],
}

SLIDES_EN = [
    {"heading": "Bounded derivative, bounded differences", "body": "One of the most useful jobs of the Mean Value Theorem."},
    {"heading": "The problem", "body": "Prove that for all real $a,b$, $$|\\sin a-\\sin b|\\le|a-b|.$$"},
    {"heading": "Key idea", "body": "Apply the MVT to $f(x)=\\sin x$ between $a$ and $b$: for some $c$, "
                                    "$$\\sin b-\\sin a=\\cos c\\,(b-a).$$ We never need to know $c$."},
    {"heading": "Proof", "body": "Assume $a<b$. $\\sin x$ is continuous on $[a,b]$ and differentiable on $(a,b)$, "
                                 "so such a $c\\in(a,b)$ exists. Since $|\\cos c|\\le 1$, "
                                 "$$|\\sin b-\\sin a|\\le|b-a|.$$"},
    {"heading": "Try it", "body": "Show the same way that $|\\arctan a-\\arctan b|\\le|a-b|$."},
]
SLIDES_ZH = [
    {"heading": "导数有界，函数差就有界", "body": "中值定理最常见的用法之一。"},
    {"heading": "题目", "body": "证明：对所有实数 $a, b$，$$|\\sin a-\\sin b|\\le|a-b|.$$"},
    {"heading": "关键想法", "body": "在 $a$ 与 $b$ 之间对 $f(x)=\\sin x$ 用中值定理：存在 $c$ 使 "
                                "$$\\sin b-\\sin a=\\cos c\\,(b-a).$$ 不需要知道 $c$ 是多少。"},
    {"heading": "证明", "body": "不妨设 $a<b$。$\\sin x$ 在 $[a,b]$ 上连续、在 $(a,b)$ 内可导，所以存在 "
                            "$c\\in(a,b)$ 满足上式。由 $|\\cos c|\\le 1$ 得 $$|\\sin b-\\sin a|\\le|b-a|.$$"},
    {"heading": "试一试", "body": "用同样的方法证明 $|\\arctan a-\\arctan b|\\le|a-b|$。"},
]
CTA_EN = "Can you show |arctan a − arctan b| ≤ |a − b| the same way? Share your idea in the comments."
CTA_ZH = "你能用同样的方法证明 |arctan a − arctan b| ≤ |a − b| 吗？欢迎在评论区写下你的思路。"
POSTS = [
    dict(platform="xiaohongshu", language="zh", status="approved",
         title_options=["MAT137 中值定理证明不等式", "sin 的差为什么不超过自变量的差？", "期中常考：中值定理经典用法"],
         caption=("很多同学学完中值定理，只记住了公式，却不知道它最常见的用途之一：把导数的上界，变成函数值之差的上界。\n\n"
                  "今天这道题：证明对所有实数 a、b，都有 |sin a − sin b| ≤ |a − b|。\n\n"
                  "思路只有一步：在 a 和 b 之间对 sin x 用中值定理，得到 sin b − sin a = cos c · (b − a)。"
                  "我们不需要知道 c 具体是多少，只需要知道 |cos c| ≤ 1。\n\n"
                  "容易忽略的地方：使用中值定理之前，要先说明函数在闭区间上连续、在开区间上可导。"
                  "sin x 在整个实数轴上都满足，但写证明时这一句不能省。\n\n" + CTA_ZH),
         hashtags=["MAT137", "微积分", "中值定理", "大学数学", "加拿大留学"],
         keywords=["中值定理", "MAT137", "微积分不等式"], cta=CTA_ZH, slides=SLIDES_ZH),
    dict(platform="instagram", language="en", status="draft",
         title_options=["MAT137: the MVT turns derivative bounds into inequalities",
                        "Why is |sin a − sin b| ≤ |a − b|?", "Midterm classic: the Mean Value Theorem trick"],
         caption=("Why is |sin a − sin b| never bigger than |a − b|?\n\n"
                  "One use of the Mean Value Theorem shows up again and again on calculus exams: a bound on the "
                  "derivative becomes a bound on differences of function values.\n\n"
                  "Apply the MVT to sin x between a and b: sin b − sin a = cos c · (b − a) for some c in between. "
                  "You never need to know c, only that |cos c| ≤ 1.\n\n"
                  "The step students skip: state the hypotheses. sin x is continuous on [a, b] and differentiable "
                  "on (a, b), so the theorem applies.\n\n" + CTA_EN),
         hashtags=["calculus", "meanvaluetheorem", "uoft", "universitymath", "mathematics"],
         keywords=["mean value theorem", "MAT137", "calculus inequality"], cta=CTA_EN, slides=SLIDES_EN),
]


def load_demo(repo: Repository) -> bool:
    """Fill an empty database with the demo content. Returns False if it already has questions."""
    if not repo.questions_df().empty:
        return False
    topics = _topic_ids(repo)

    repo.add_offering("uoft_mat137", 2026, "Fall", monitor=True,
                      notes="Demo term (no real course page linked)")
    repo.add_source(source_type="other", title="Demo: why does the MVT need continuity on the closed interval?",
                    content_excerpt="Fictional example for the demo: a student asks why the theorem needs "
                                    "continuity on [a, b] and not just on (a, b).",
                    engagement_count=4, notes="Fictional demo entry", topic_ids=[topics["Mean Value Theorem"]])
    repo.add_source(source_type="other", title="Demo: choosing delta that depends on x",
                    content_excerpt="Fictional example for the demo: a student's proof picks δ in terms of x.",
                    engagement_count=6, notes="Fictional demo entry",
                    topic_ids=[topics["Epsilon–delta definition of a limit"]])

    ids = {}
    for q in QUESTIONS:
        fields = {k: q[k] for k in ("title", "question_latex", "question_type", "difficulty", "expected_time_min",
                                    "target_audience", "proposed_solution", "teaching_insight")}
        [qid] = repo.add_questions([{**fields, "topic_id": topics[q["topic"]]}], batch_id=DEMO_BATCH,
                                   generation_model="demo", prompt_version="demo")
        repo.set_question_status(qid, selection_status=q["status"])
        ids[q["key"]] = qid

    sid, _ = repo.save_solution(ids["mvt"], **SOLUTION)
    repo.set_math_review(sid, json.dumps(REVIEW, ensure_ascii=False), "ai_checked")
    repo.set_solution_approved(sid, True)

    drafts = []
    for p in POSTS:
        post = {k: v for k, v in p.items() if k != "status"}
        post["title"] = post["title_options"][0]
        post["flags"] = check_post(p["platform"], cta_style="soft", **{
            k: post[k] for k in ("title", "title_options", "caption", "hashtags", "cta", "slides")})
        drafts.append(post)
    post_ids = repo.add_posts(sid, drafts, draft_round="round_demo", cta_style="soft")
    for pid, p in zip(post_ids, POSTS):
        repo.update_post(pid, status=p["status"])
    return True
