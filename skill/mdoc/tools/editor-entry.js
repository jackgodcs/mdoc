import {defaultKeymap, history, historyKeymap, indentWithTab} from "@codemirror/commands";
import {markdown} from "@codemirror/lang-markdown";
import {bracketMatching, defaultHighlightStyle, indentOnInput, syntaxHighlighting} from "@codemirror/language";
import {EditorState, StateEffect, StateField} from "@codemirror/state";
import {Decoration, drawSelection, dropCursor, EditorView, highlightActiveLine, highlightActiveLineGutter, highlightSpecialChars, keymap, lineNumbers, rectangularSelection} from "@codemirror/view";
import {highlightSelectionMatches, searchKeymap} from "@codemirror/search";

window.MdocEditor = {
  create(parent, options = {}) {
    const setHighlights = StateEffect.define();
    const setFinding = StateEffect.define();
    const highlights = StateField.define({
      create: () => Decoration.none,
      update(value, transaction) {
        value = value.map(transaction.changes);
        for (const effect of transaction.effects) if (effect.is(setHighlights)) value = Decoration.set(effect.value.map(range => Decoration.mark({class: "cm-auto-fix", attributes: {"data-rule": range.rules.join(" ")}}).range(range.from, Math.max(range.from + 1, range.to))), true);
        return value;
      },
      provide: field => EditorView.decorations.from(field),
    });
    const findingHighlight = StateField.define({
      create: () => Decoration.none,
      update(value, transaction) {
        value = value.map(transaction.changes);
        for (const effect of transaction.effects) if (effect.is(setFinding)) value = effect.value ? Decoration.set([effect.value.line ? Decoration.line({class: "cm-finding-line"}).range(effect.value.from) : Decoration.mark({class: "cm-finding-hit"}).range(effect.value.from, effect.value.to)]) : Decoration.none;
        return value;
      },
      provide: field => EditorView.decorations.from(field),
    });
    const changes = EditorView.updateListener.of(update => {
      if (update.docChanged) options.onChange?.(update.state.doc.toString());
      if (update.selectionSet) { const line = update.state.doc.lineAt(update.state.selection.main.head); options.onCursor?.(line.number, update.state.selection.main.head - line.from + 1); }
    });
    const view = new EditorView({
      parent,
      state: EditorState.create({
        doc: options.content || "",
        extensions: [
          lineNumbers(), highlightActiveLineGutter(), highlightSpecialChars(), history(),
          drawSelection(), dropCursor(), indentOnInput(), bracketMatching(),
          rectangularSelection(), highlightActiveLine(), highlightSelectionMatches(), markdown(),
          syntaxHighlighting(defaultHighlightStyle, {fallback: true}), EditorView.lineWrapping,
          EditorState.readOnly.of(Boolean(options.readOnly)), EditorView.editable.of(!options.readOnly),
          EditorView.theme({"&": {height: "100%", fontSize: "13px"}, ".cm-scroller": {fontFamily: "Consolas, monospace", overflow: "auto"}, ".cm-content": {padding: "10px 0"}, ".cm-auto-fix": {backgroundColor: "#fff2a8", boxShadow: "inset 0 -1px #d5a400"}, ".cm-finding-hit": {backgroundColor: "#ffd666 !important", boxShadow: "inset 0 -1px #b7791f !important"}, ".cm-line.cm-finding-line": {backgroundColor: "#ffe58a !important", boxShadow: "inset 4px 0 #b7791f !important"}}),
          keymap.of([...defaultKeymap, ...historyKeymap, ...searchKeymap, indentWithTab]), highlights, findingHighlight, changes,
        ],
      }),
    });
    const position = (line, column) => {
      const target = view.state.doc.line(Math.min(Math.max(1, line || 1), view.state.doc.lines));
      return Math.min(target.to, target.from + Math.max(0, (column || 1) - 1));
    };
    const cursorEffects = anchor => {
      const coords = view.coordsAtPos(anchor), bounds = view.scrollDOM.getBoundingClientRect();
      return coords && coords.top >= bounds.top && coords.bottom <= bounds.bottom ? [] : EditorView.scrollIntoView(anchor, {y: "center"});
    };
    return {
      destroy: () => view.destroy(),
      focus: () => view.focus(),
      getSelection() {
        const selection = view.state.selection.main, from = Math.min(selection.anchor, selection.head), to = Math.max(selection.anchor, selection.head), first = view.state.doc.lineAt(from), last = view.state.doc.lineAt(to);
        return {from, to, text: view.state.doc.sliceString(from, to), line: first.number, column: from - first.from + 1, end_line: last.number, end_column: to - last.from + 1};
      },
      getValue: () => view.state.doc.toString(),
      setValue(content, ranges = []) { view.dispatch({changes: {from: 0, to: view.state.doc.length, insert: content}, effects: setHighlights.of(ranges), userEvent: "input.auto-fix"}); },
      replaceRange(from, to, content) { view.dispatch({changes: {from, to, insert: content}, selection: {anchor: from + content.length}, userEvent: "input.translation"}); },
      clearHighlights() { view.dispatch({effects: setHighlights.of([])}); },
      clearFindingHighlight() { view.dispatch({effects: setFinding.of(null)}); },
      setCursorOffset(offset) {
        const anchor = Math.min(Math.max(0, offset), view.state.doc.length);
        view.focus(); view.dispatch({selection: {anchor}, effects: cursorEffects(anchor)}); const line = view.state.doc.lineAt(anchor), result = {line: line.number, column: anchor - line.from + 1}; options.onCursor?.(result.line, result.column); return result;
      },
      setOffset(from, to = from + 1) { const anchor = Math.min(from, view.state.doc.length), head = Math.min(Math.max(from + 1, to), view.state.doc.length); view.focus(); view.dispatch({selection: {anchor: head, head: anchor}, effects: EditorView.scrollIntoView(anchor, {y: "center"})}); const line = view.state.doc.lineAt(anchor), result = {line: line.number, column: anchor - line.from + 1}; options.onCursor?.(result.line, result.column); return result; },
      setCursor(line, column) {
        const anchor = position(line, column);
        if (!options.readOnly) view.focus(); view.dispatch({selection: {anchor}, effects: cursorEffects(anchor)}); const current = view.state.doc.lineAt(anchor); options.onCursor?.(current.number, anchor - current.from + 1);
      },
      setSelection(line, column, endLine, endColumn) {
        const anchor = position(line, column), head = position(endLine || line, endColumn || column);
        if (!options.readOnly) view.focus(); view.dispatch({selection: {anchor, head}, effects: EditorView.scrollIntoView(head, {y: "center"})}); const current = view.state.doc.lineAt(head); options.onCursor?.(current.number, head - current.from + 1);
      },
      setFinding(line, column, endLine, endColumn) {
        if (!view.state.doc.length) { view.dispatch({effects: setFinding.of(null)}); return; }
        const anchor = position(line, column), head = position(endLine || line, endColumn || column), from = Math.min(anchor, head), to = Math.max(anchor, head);
        if (from === to) view.dispatch({effects: setFinding.of({from: view.state.doc.lineAt(from).from, line: true})});
        else view.dispatch({effects: setFinding.of({from, to})});
      },
    };
  },
};
