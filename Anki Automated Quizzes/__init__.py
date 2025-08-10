from aqt import mw
from aqt.qt import (
    QAction,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QSpinBox,
    QListWidget,
    QListWidgetItem,
    QWidget,
    QCheckBox,
    QMessageBox,
)
from aqt.utils import tooltip
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog, QSizePolicy, QRadioButton, QFrame, QScrollArea
import random
import os
import json
import re
from functools import partial

# ---- Cross-version helpers ----
def _deck_tuple(dni):
    if hasattr(dni, "id") and hasattr(dni, "name"):
        return (dni.id, dni.name)
    try:
        return (dni["id"], dni["name"])
    except Exception:
        return (getattr(dni, "id", None), str(dni))

def _get_all_decks():
    try:
        items = mw.col.decks.all_names_and_ids()
    except Exception:
        items = mw.col.decks.allNamesAndIds()
    return [_deck_tuple(d) for d in items]

def _note_type_obj(note):
    try:
        return note.note_type()  # New API
    except Exception:
        return note.model()      # Old API

def _note_type_name(note):
    nt = _note_type_obj(note)
    try:
        return nt.name
    except Exception:
        try:
            return nt["name"]
        except Exception:
            return str(nt)

def _field_names_for_model(model_obj):
    try:
        return list(model_obj.field_names())
    except Exception:
        pass
    try:
        return [f["name"] for f in model_obj["flds"]]
    except Exception:
        return []

def _find_notes_in_deck(deck_name, exclude_tags):
    tag_filter = " ".join(f'-tag:"{t}"' for t in exclude_tags if t)
    query = f'deck:"{deck_name}" {tag_filter}'.strip()
    return mw.col.find_notes(query)

def _collect_models_and_fields(nids):
    """Return mapping: model_name -> (model_obj, field_names)."""
    res = {}
    for nid in nids:
        n = mw.col.get_note(nid)
        if not n:
            continue
        mobj = _note_type_obj(n)
        try:
            mname = mobj.name
        except Exception:
            mname = mobj.get("name") if isinstance(mobj, dict) else str(mobj)
        if mname in res:
            continue
        fields = _field_names_for_model(mobj)
        res[mname] = (mobj, fields)
    return res

def _strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.IGNORECASE)
    text = re.sub(r"</?[^>]+>", "", text or "")
    return text.strip()

def _normalize_html(s: str) -> str:
    """Normalize for equality checks: collapse whitespace, lower, strip."""
    s = (s or "").replace("\r", "").replace("\n", "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

def _notes_to_qa(notes, prompt_field, answer_field, required_model_name=None):
    qa = []
    for nid in notes:
        n = mw.col.get_note(nid)
        if n is None:
            continue
        if required_model_name and _note_type_name(n) != required_model_name:
            continue
        if prompt_field not in n or answer_field not in n:
            continue
        front = (n[prompt_field] or "").strip()
        back = (n[answer_field] or "").strip()
        if front and back:
            qa.append({"nid": nid, "prompt": front, "answer": back})
    return qa

def _make_quiz_items(qa, num_questions, num_choices, allow_answer_reuse: bool):
    if len(qa) == 0:
        raise ValueError("No notes found to generate questions.")
    pool = qa[:]
    random.shuffle(pool)
    selected = pool[:min(num_questions, len(pool))]
    all_answers = [x["answer"] for x in qa]

    quiz = []
    for item in selected:
        correct = item["answer"]
        options = [correct]

        if allow_answer_reuse:
            unique_others = [a for a in set(all_answers) if _normalize_html(a) != _normalize_html(correct)]
            random.shuffle(unique_others)
            options += unique_others[:max(0, num_choices - 1)]
            while len(options) < num_choices:
                options.append(random.choice(all_answers))
        else:
            candidates = [a for a in set(all_answers) if _normalize_html(a) != _normalize_html(correct)]
            random.shuffle(candidates)
            options += candidates[:max(0, num_choices - 1)]

        options = options[:num_choices]
        random.shuffle(options)

        quiz.append({
            "nid": item["nid"],
            "prompt": item["prompt"],     # raw HTML allowed
            "correct": correct,           # raw HTML allowed
            "options": options,           # list of raw HTML strings
        })
    return quiz

# Quiz history helpers
def _history_path():
    addon_folder = os.path.dirname(__file__)
    return os.path.join(addon_folder, "quiz_history.json")

def _load_history():
    try:
        with open(_history_path(), "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()

def _save_history(nid_list):
    history = _load_history()
    history.update(nid_list)
    with open(_history_path(), "w", encoding="utf-8") as f:
        json.dump(list(history), f)

# ---- Option row widget (Radio + HTML label) ----
class OptionRow(QWidget):
    def __init__(self, html_text: str, parent=None):
        super().__init__(parent)
        self.raw_html = html_text or ""
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 4, 0, 4)
        self.radio = QRadioButton(self)
        row.addWidget(self.radio, 0)
        self.label = QLabel(self)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.label.setOpenExternalLinks(True)
        self.label.setWordWrap(True)
        # Render raw HTML; if actually empty, show a placeholder
        self.label.setText(self.raw_html if self.raw_html.strip() else "<i>(blank)</i>")
        self.label.setMinimumWidth(400)
        self.label.setMaximumWidth(700)
        row.addWidget(self.label, 1)

        # Allow clicking the label to toggle the radio
        self.label.mousePressEvent = lambda e: self.radio.setChecked(True)

    def set_enabled(self, enabled: bool):
        self.radio.setEnabled(enabled)
        self.label.setEnabled(enabled)

    def set_background(self, color_css: str):
        self.setStyleSheet(f"QWidget {{ background: {color_css}; border-radius: 6px; }}")

class MCQuizDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Automated Quiz")
        self.resize(900, 700)

        self.cfg = mw.addonManager.getConfig(__name__) or {}
        self.cfg.setdefault("default_deck", "")
        self.cfg.setdefault("num_choices", 4)
        self.cfg.setdefault("num_questions", 25)
        self.cfg.setdefault("exclude_tags", [])
        self.cfg.setdefault("allow_answer_reuse", True)
        self.cfg.setdefault("last_model_name", "")
        self.cfg.setdefault("last_prompt_field", "")
        self.cfg.setdefault("last_answer_field", "")
        self.cfg.setdefault("num_per_page", 5)

        layout = QVBoxLayout(self)

        # --- Config panel ---
        self.config_widget = QWidget(self)
        config_layout = QVBoxLayout(self.config_widget)

        decks = _get_all_decks()
        self.deck_cb = QComboBox(self.config_widget)
        names = [name for (_id, name) in decks]
        self.deck_cb.addItems(names)
        if self.cfg["default_deck"] and self.cfg["default_deck"] in names:
            self.deck_cb.setCurrentText(self.cfg["default_deck"])

        config_layout.addWidget(QLabel("Deck:"))
        config_layout.addWidget(self.deck_cb)

        config_layout.addWidget(QLabel("Note type:"))
        self.model_cb = QComboBox(self.config_widget)
        config_layout.addWidget(self.model_cb)

        pf_row = QHBoxLayout()
        pf_row.addWidget(QLabel("Prompt field:"))
        self.prompt_cb = QComboBox(self.config_widget)
        pf_row.addWidget(self.prompt_cb)
        pf_row.addWidget(QLabel("Answer field:"))
        self.answer_cb = QComboBox(self.config_widget)
        pf_row.addWidget(self.answer_cb)
        config_layout.addLayout(pf_row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Questions:"))
        self.qcount = QSpinBox(self.config_widget); self.qcount.setRange(1, 1000); self.qcount.setValue(int(self.cfg["num_questions"]))
        row.addWidget(self.qcount)
        row.addWidget(QLabel("Choices:"))
        self.ccount = QSpinBox(self.config_widget); self.ccount.setRange(2, 10); self.ccount.setValue(int(self.cfg["num_choices"]))
        row.addWidget(self.ccount)
        row.addWidget(QLabel("Questions per page:"))
        self.qperpage = QSpinBox(self.config_widget); self.qperpage.setRange(1, 20); self.qperpage.setValue(int(self.cfg.get("num_per_page", 5)))
        row.addWidget(self.qperpage)
        config_layout.addLayout(row)

        self.dup_cb = QCheckBox("Allow answer reuse", self.config_widget)
        self.dup_cb.setChecked(bool(self.cfg["allow_answer_reuse"]))
        config_layout.addWidget(self.dup_cb)

        config_layout.addWidget(QLabel("Exclude tags (optional):"))
        self.tags_list = QListWidget(self.config_widget)
        for t in self.cfg["exclude_tags"]:
            self.tags_list.addItem(QListWidgetItem(t))
        config_layout.addWidget(self.tags_list)

        self.exclude_history_cb = QCheckBox("Exclude cards from previous quizzes", self.config_widget)
        self.exclude_history_cb.setChecked(False)
        config_layout.addWidget(self.exclude_history_cb)

        self.clear_history_btn = QPushButton("Clear Quiz History", self.config_widget)
        self.clear_history_btn.clicked.connect(self._on_clear_history)
        config_layout.addWidget(self.clear_history_btn)

        self.start_btn = QPushButton("Start Quiz", self.config_widget)
        self.start_btn.clicked.connect(self.start_quiz)
        config_layout.addWidget(self.start_btn)

        layout.addWidget(self.config_widget)

        # --- Quiz container inside a scroll area ---
        self.quiz_widget = QWidget()
        self.quiz_container = QVBoxLayout(self.quiz_widget)
        self.quiz_widget.setLayout(self.quiz_container)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.quiz_widget)
        layout.addWidget(self.scroll_area)

        # Nav buttons
        self.next_btn = QPushButton("Next Page")
        self.next_btn.clicked.connect(self._on_next_page)
        self.next_btn.hide()
        layout.addWidget(self.next_btn)

        self.prev_btn = QPushButton("Previous Page")
        self.prev_btn.clicked.connect(self._on_prev_page)
        self.prev_btn.hide()
        layout.addWidget(self.prev_btn)

        # Signals
        self.deck_cb.currentTextChanged.connect(self._on_deck_changed)
        self.model_cb.currentTextChanged.connect(self._on_model_changed)

        # Init
        self._on_deck_changed(self.deck_cb.currentText())

        self.state = {"quiz": [], "idx": 0, "correct": 0, "total": 0, "page": 0, "per_page": 1}
        self.current_question_widgets = []
        self.user_answers = {}  # quiz index -> chosen raw html

    # ---- UI updates ----
    def _on_deck_changed(self, deck_name):
        nids = _find_notes_in_deck(deck_name, [])
        models = _collect_models_and_fields(nids)
        self.model_cb.blockSignals(True)
        self.model_cb.clear()
        for mname in sorted(models.keys()):
            self.model_cb.addItem(mname)
        self.model_cb.blockSignals(False)

        if self.cfg.get("last_model_name") in models:
            self.model_cb.setCurrentText(self.cfg["last_model_name"])

        self._populate_fields(deck_models=models)

    def _populate_fields(self, deck_models=None):
        deck_name = self.deck_cb.currentText()
        if deck_models is None:
            nids = _find_notes_in_deck(deck_name, [])
            deck_models = _collect_models_and_fields(nids)

        mname = self.model_cb.currentText()
        fields = []
        if mname in deck_models:
            fields = deck_models[mname][1]

        self.prompt_cb.blockSignals(True)
        self.answer_cb.blockSignals(True)
        self.prompt_cb.clear()
        self.answer_cb.clear()
        for f in fields:
            self.prompt_cb.addItem(f)
            self.answer_cb.addItem(f)

        if self.cfg.get("last_prompt_field") in fields:
            self.prompt_cb.setCurrentText(self.cfg["last_prompt_field"])
        else:
            for guess in ("Front", "Question", "Prompt"):
                if guess in fields:
                    self.prompt_cb.setCurrentText(guess); break
        if self.cfg.get("last_answer_field") in fields:
            self.answer_cb.setCurrentText(self.cfg["last_answer_field"])
        else:
            for guess in ("Back", "Answer", "Response"):
                if guess in fields:
                    self.answer_cb.setCurrentText(guess); break

        self.prompt_cb.blockSignals(False)
        self.answer_cb.blockSignals(False)

    def _on_model_changed(self, _text):
        self._populate_fields()

    # ---- Quiz flow ----
    def start_quiz(self):
        deck = self.deck_cb.currentText()
        exclude = [self.tags_list.item(i).text() for i in range(self.tags_list.count())]
        num_q = int(self.qcount.value())
        num_c = int(self.ccount.value())
        allow_dup = bool(self.dup_cb.isChecked())

        model_name = self.model_cb.currentText()
        prompt_field = self.prompt_cb.currentText()
        answer_field = self.answer_cb.currentText()

        nids = _find_notes_in_deck(deck, exclude)
        if self.exclude_history_cb.isChecked():
            used_nids = _load_history()
            nids = [nid for nid in nids if nid not in used_nids]
        qa = _notes_to_qa(nids, prompt_field, answer_field, required_model_name=model_name)

        if len(qa) == 0:
            QMessageBox.warning(self, "No matching notes",
                                "No notes found with the chosen fields in this deck.\n"
                                f"Deck: {deck}\nNote type: {model_name}\nFields: {prompt_field} / {answer_field}")
            return

        try:
            quiz = _make_quiz_items(qa, num_q, num_c, allow_dup)
        except Exception as e:
            QMessageBox.warning(self, "Quiz error",
                                f"Could not build quiz: {e}\n"
                                f"Notes available: {len(qa)}")
            return

        # persist choices
        self.cfg["default_deck"] = deck
        self.cfg["num_choices"] = num_c
        self.cfg["num_questions"] = num_q
        self.cfg["allow_answer_reuse"] = allow_dup
        self.cfg["last_model_name"] = model_name
        self.cfg["last_prompt_field"] = prompt_field
        self.cfg["last_answer_field"] = answer_field
        self.cfg["num_per_page"] = int(self.qperpage.value())
        try:
            mw.addonManager.writeConfig(__name__, self.cfg)
        except Exception:
            pass

        random.shuffle(quiz)
        self.state = {
            "quiz": quiz,
            "idx": 0,
            "correct": 0,
            "total": len(quiz),
            "page": 0,
            "per_page": int(self.qperpage.value()),
        }
        self.user_answers = {}
        self.config_widget.hide()
        self._show_current_page()

    def _clear_quiz_container(self):
        for widget in self.current_question_widgets:
            widget.setParent(None)
        self.current_question_widgets = []

    def _show_current_page(self):
        self._clear_quiz_container()
        quiz = self.state["quiz"]
        idx = self.state["idx"]
        per_page = self.state["per_page"]
        total = self.state["total"]

        if idx >= total:
            self.next_btn.hide()
            self.prev_btn.hide()
            self._show_results_page()
            return

        end = min(idx + per_page, total)
        self.page_option_rows = []

        for i, qidx in enumerate(range(idx, end)):
            q = quiz[qidx]
            q_group = QVBoxLayout()
            q_label = QLabel(f"Q{qidx+1}: {_strip_html(q['prompt'])}")
            q_label.setWordWrap(True)
            q_label.setMaximumWidth(820)
            q_group.addWidget(q_label)

            rows = []
            for opt in q["options"]:
                row = OptionRow(opt, self)
                # clicking the radio selects and finalizes the question
                row.radio.toggled.connect(partial(self._on_choose, i, row))
                q_group.addWidget(row)
                rows.append(row)
                self.current_question_widgets.append(row)
            self.page_option_rows.append(rows)

            group_widget = QWidget()
            group_widget.setLayout(q_group)
            self.quiz_container.addWidget(group_widget)
            self.current_question_widgets.append(q_label)
            self.current_question_widgets.append(group_widget)

        if end < total:
            self.next_btn.setText("Next Page")
            self.next_btn.show()
        else:
            self.next_btn.setText("Finish")
            self.next_btn.show()

        if idx > 0:
            self.prev_btn.show()
        else:
            self.prev_btn.hide()

        # --- Auto-scroll to top ---
        self.scroll_area.verticalScrollBar().setValue(0)

    def _on_choose(self, question_idx_in_page, chosen_row: OptionRow, checked: bool):
        if not checked:
            return
        quiz = self.state["quiz"]
        idx = self.state["idx"]
        qidx = idx + question_idx_in_page
        if qidx in self.user_answers:
            return  # already answered

        q = quiz[qidx]
        chosen_raw = chosen_row.raw_html
        correct_raw = q["correct"]

        # record
        self.user_answers[qidx] = chosen_raw

        # peer rows for this question
        rows = self.page_option_rows[question_idx_in_page]
        # lock and colorize
        for row in rows:
            row.set_enabled(False)
            if _normalize_html(row.raw_html) == _normalize_html(correct_raw):
                row.set_background("#1f6f1f")  # green
            elif row is chosen_row:
                row.set_background("#7f1f1f")  # red

        if _normalize_html(chosen_raw) == _normalize_html(correct_raw):
            self.state["correct"] += 1

    def _on_next_page(self):
        self.state["idx"] += self.state["per_page"]
        self._show_current_page()

    def _on_prev_page(self):
        self.state["idx"] = max(0, self.state["idx"] - self.state["per_page"])
        self._show_current_page()

    def _show_results_page(self):
        self._clear_quiz_container()
        quiz = self.state["quiz"]
        total = self.state["total"]
        correct = self.state["correct"]

        pct = round(100 * correct / max(1, total))
        summary = QLabel(f"<b>Quiz Complete!</b><br>Score: {correct}/{total} ({pct}%)")
        self.quiz_container.addWidget(summary)
        self.current_question_widgets.append(summary)

        # results table (text-only for readability)
        html = "<table border=1 cellpadding=4><tr><th>#</th><th>Prompt</th><th>Your Answer</th><th>Correct Answer</th></tr>"
        for i, q in enumerate(quiz):
            ua_raw = self.user_answers.get(i, "")
            ua_txt = _strip_html(ua_raw)
            ca_txt = _strip_html(q["correct"])
            color = "#cfc" if _normalize_html(ua_raw) == _normalize_html(q["correct"]) else "#fcc"
            prompt_txt = _strip_html(q["prompt"])
            html += f"<tr style='background:{color}'><td>{i+1}</td><td>{prompt_txt}</td><td>{ua_txt}</td><td>{ca_txt}</td></tr>"
        html += "</table>"

        results_label = QLabel()
        results_label.setTextFormat(Qt.TextFormat.RichText)
        results_label.setText(html)
        results_label.setWordWrap(True)
        self.quiz_container.addWidget(results_label)
        self.current_question_widgets.append(results_label)

        export_btn = QPushButton("Export Results to HTML")
        export_btn.clicked.connect(self._export_results_html)
        self.quiz_container.addWidget(export_btn)
        self.current_question_widgets.append(export_btn)

        _save_history([q["nid"] for q in quiz])
        self.config_widget.show()

    def _export_results_html(self):
        quiz = self.state["quiz"]
        total = self.state["total"]
        correct = self.state["correct"]
        pct = round(100 * correct / max(1, total))

        html = f"<h2>Quiz Results</h2><p>Score: {correct}/{total} ({pct}%)</p>"
        html += "<table border=1 cellpadding=4><tr><th>#</th><th>Prompt</th><th>Your Answer</th><th>Correct Answer</th></tr>"
        for i, q in enumerate(quiz):
            ua_raw = self.user_answers.get(i, "")
            ua_txt = _strip_html(ua_raw)
            ca_txt = _strip_html(q["correct"])
            color = "#cfc" if _normalize_html(ua_raw) == _normalize_html(q["correct"]) else "#fcc"
            prompt_txt = _strip_html(q["prompt"])
            html += f"<tr style='background:{color}'><td>{i+1}</td><td>{prompt_txt}</td><td>{ua_txt}</td><td>{ca_txt}</td></tr>"
        html += "</table>"

        fname, _ = QFileDialog.getSaveFileName(self, "Save Results", "quiz_results.html", "HTML Files (*.html)")
        if fname:
            with open(fname, "w", encoding="utf-8") as f:
                f.write(html)
            tooltip("Results exported.")

        retry_btn = QPushButton("Retry Quiz")
        retry_btn.clicked.connect(self.retry_quiz)
        self.quiz_container.addWidget(retry_btn)
        self.current_question_widgets.append(retry_btn)

    def retry_quiz(self):
        self.state = {"quiz": [], "idx": 0, "correct": 0, "total": 0, "page": 0, "per_page": 5}
        self.user_answers = {}
        self.config_widget.show()
        self.next_btn.hide()
        self.prev_btn.hide()

    def _on_clear_history(self):
        path = _history_path()
        if os.path.exists(path):
            try:
                os.remove(path)
                tooltip("Quiz history cleared.")
            except Exception as e:
                tooltip(f"Error clearing quiz history: {e}")

def show_quiz_dialog():
    dlg = MCQuizDialog(mw)
    dlg.exec()

action = QAction("Automated Quizzes", mw)
action.triggered.connect(show_quiz_dialog)
mw.form.menuTools.addAction(action)
