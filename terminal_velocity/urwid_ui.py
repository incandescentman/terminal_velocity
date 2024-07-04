"""A console user interface for Terminal Velocity.

Implemented using the console user interface library urwid.

"""
import sys
import subprocess
import shlex
import logging
import urwid
from . import notebook

logger = logging.getLogger(__name__)

palette = [
    ("placeholder", "dark blue", "default"),
    ("notewidget unfocused", "default", "default"),
    ("notewidget focused", "black", "brown"),
    ("search", "default", "default"),
    ("autocomplete", "black", "brown"),
]


def system(cmd, loop):
    """Execute a system command in a subshell and return the exit status."""

    loop.screen.stop()

    cmd = f"{cmd}"
    cmd = cmd.encode(sys.getfilesystemencoding())  # FIXME: Correct encoding?

    try:
        if isinstance(cmd, bytes):
            cmd = cmd.decode('utf-8')

        # Use shell=True to handle complex commands (like pipes)
        returncode = subprocess.call(cmd, shell=True)
    except Exception as e:
        logger.exception(e)
        raise e
    finally:
        loop.screen.start()

    return returncode


def placeholder_text(text):
    """Return a placeholder text widget with the given text."""

    text_widget = urwid.Text(("placeholder", text), align="center")
    filler_widget = urwid.Filler(text_widget)
    return filler_widget


class NoteWidget(urwid.Text):

    def __init__(self, note):
        self.note = note
        super().__init__(note.title)

    def selectable(self):
        return True

    def keypress(self, size, key):
        return key

    def render(self, size, focus=False):
        """Render the widget applying focused and unfocused display attrs."""

        if focus:
            attr_map = {None: "notewidget focused"}
        else:
            attr_map = {None: "notewidget unfocused"}
        canv = super().render(size, focus=focus)
        canv = urwid.CompositeCanvas(canv)
        canv.fill_attr_apply(attr_map)
        return canv


class AutocompleteWidget(urwid.Edit):
    """A text editing widget with autocomplete support.

    If you set the .autocomplete_text attribute, it will be shown to the user
    as an autocomplete suggestion.

    Also has a .fake_focus attribute that, if set to True, makes the widget
    look like it has the keyboard focus even when it doesn't.

    """
    def __init__(self, *args, **kwargs):
        self.fake_focus = True
        self._autocomplete_text = None
        super().__init__(*args, **kwargs)

    @property
    def autocomplete_text(self):
        return self._autocomplete_text

    @autocomplete_text.setter
    def autocomplete_text(self, text):
        self._autocomplete_text = text
        self._invalidate()

    def render(self, size, focus=False):
        return super().render(size, self.fake_focus)

    def get_text(self):

        # When search bar is empty show placeholder text.
        if not self.edit_text and not self.autocomplete_text:
            placeholder_text = "Find or Create"
            return (placeholder_text,
                    [("placeholder", len(placeholder_text))])

        # When no note is focused simply show typed text in search bar.
        if not self.autocomplete_text:
            return super().get_text()

        # When a note is focused show it's title in the search bar.
        is_substring = self.autocomplete_text.lower().startswith(
                self.edit_text.lower())
        if self.edit_text and is_substring:
            # If the typed text is a substring of the focused note's title,
            # then show the typed text followed by the rest of the focused
            # note's title in a different color.
            text_to_show = self.edit_text + self.autocomplete_text[
                    len(self.edit_text):]
            attrs = [("search", len(self.edit_text)),
                    ("autocomplete", len(text_to_show) - len(self.edit_text))]
            return (text_to_show, attrs)
        else:
            # If the typed text is not a prefix of the focused note's title,
            # just show the focused note's title in the search bar.
            return (self.autocomplete_text,
                    [('autocomplete', len(self.autocomplete_text))])

    def consume(self):
        """Consume the autocomplete text, turning it into typed text."""

        if self.autocomplete_text and (
                len(self.edit_text) < len(self.autocomplete_text)):
            self.set_edit_text(self.autocomplete_text)
            self.move_cursor_to_coords((1,), len(self.autocomplete_text), 0)
            self.autocomplete_text = None
            return True
        else:
            return False


class NoteFilterListBox(urwid.ListBox):
    """A filterable list of notes from a notebook."""

    def __init__(self, on_changed=None):
        """Initialize a new NoteFilterListBox.

        Keyword arguments:
        on_changed -- callable that will be called when the focused note
            changes, the new focused note will be passed as argument

        """
        self._fake_focus = False
        self.list_walker = urwid.SimpleFocusListWalker([])
        self.widgets = {}  # NoteWidget cache.
        super().__init__(self.list_walker)
        self.on_changed = on_changed

    @property
    def selected_note(self):
        return self.focus.note

    @property
    def fake_focus(self):
        return self._fake_focus

    @fake_focus.setter
    def fake_focus(self, value):
        self._fake_focus = value
        self._invalidate()

    def render(self, size, focus=False):
        if len(self.list_walker) == 0:
            placeholder = placeholder_text("No matching notes, press Enter "
                "to create a new note")
            return placeholder.render(size)
        return super().render(size, self.fake_focus)

    def filter(self, matching_notes):
        """Filter this listbox to show only widgets for matching notes."""

        matching_widgets = []
        for note in matching_notes:
            widget = self.widgets.get(note.abspath)
            if widget:
                matching_widgets.append(widget)
            else:
                widget = NoteWidget(note)
                self.widgets[note.abspath] = widget
                matching_widgets.append(widget)

        del self.list_walker[:]

        for widget in matching_widgets:
            self.list_walker.append(widget)

    def focus_note(self, note):
        """Focus the widget for the given note."""

        for widget in self.list_walker:
            if widget.note == note:
                self.list_walker.set_focus(self.list_walker.index(widget))
                break

    def keypress(self, size, key):
        result = super().keypress(size, key)
        self.on_changed(self.selected_note)
        return result

    def mouse_event(self, size, event, button, col, row, focus):
        result = super().mouse_event(
                size, event, button, col, row, focus)
        self.on_changed(self.selected_note)
        return result


class MainFrame(urwid.Frame):
    """The topmost urwid widget."""

    def __init__(self, notes_dir, editor, extension, extensions, exclude=None):

        self.editor = editor
        self.notebook = notebook.PlainTextNoteBook(notes_dir, extension,
                extensions, exclude=exclude)

        self.suppress_filter = False
        self.suppress_focus = False

        self._selected_note = None

        self.search_box = AutocompleteWidget(wrap="clip")
        self.list_box = NoteFilterListBox(on_changed=self.on_list_box_changed)

        urwid.connect_signal(self.search_box, "change",
                self.on_search_box_changed)

        super().__init__(
                header=urwid.LineBox(self.search_box),
                body=None,
                focus_part="body")

        self.filter(self.search_box.edit_text)

    @property
    def selected_note(self):
        return self._selected_note

    @selected_note.setter
    def selected_note(self, note):
        """Select the given note.

        Make the note appear focused in the list box, and the note's title
        autocompleted in the search box.

        """
        if self.suppress_focus:
            return

        if note:

            self.search_box.autocomplete_text = note.title

            self.list_box.fake_focus = True

            self.list_box.focus_note(note)

        else:

            self.search_box.autocomplete_text = None

            self.list_box.fake_focus = False

        self._selected_note = note

    def quit(self):
        """Quit the app."""

        raise urwid.ExitMainLoop()

    def keypress(self, size, key):

        maxcol, maxrow = size

        self.suppress_filter = False
        self.suppress_focus = False

        if key in ["esc", "ctrl d"]:
            if self.selected_note:
                self.selected_note = None
                return None
            elif self.search_box.edit_text:
                self.search_box.set_edit_text("")
                return None

        elif key in ["enter"]:
            if self.selected_note:
                system(self.editor + ' ' + shlex.quote(self.selected_note.abspath), self.loop)
            else:
                if self.search_box.edit_text:
                    try:
                        note = self.notebook.add_new(self.search_box.edit_text)
                        system(self.editor + ' ' + shlex.quote(note.abspath), self.loop)
                    except notebook.NoteAlreadyExistsError:
                        system(self.editor + ' ' + shlex.quote(self.search_box.edit_text +
                                self.notebook.extension),
                            self.loop)
                    except notebook.InvalidNoteTitleError:
                        pass
                else:
                    pass
            self.suppress_focus = True


            self.filter(self.search_box.edit_text)
            return None

        elif key in ["ctrl x"]:
            self.quit()

        elif self.selected_note and key in ["tab", "left", "right"]:
            if self.search_box.consume():
                return None
            else:
                return self.search_box.keypress((maxcol,), key)

        elif key in ["down"]:
            if not self.list_box.fake_focus:
                self.list_box.fake_focus = True
                self.on_list_box_changed(self.list_box.selected_note)
                return None
            else:
                return self.list_box.keypress(size, key)

        elif key in ["up", "page up", "page down"]:
            return self.list_box.keypress(size, key)

        elif key in ["backspace"]:
            consume = False
            if self.selected_note:
                if self.search_box.edit_text == "":
                    consume = True
                else:
                    title = self.selected_note.title.lower()
                    typed = self.search_box.edit_text.lower()
                    if not title.startswith(typed):
                        consume = True
            if consume:
                self.search_box.consume()
            else:
                self.selected_note = None
            self.suppress_focus = True
            return self.search_box.keypress((maxcol,), key)

        else:
            return self.search_box.keypress((maxcol,), key)

    def filter(self, query):
        """Do the synchronized list box filter and search box autocomplete."""

        if self.suppress_filter:
            return

        if len(self.notebook) == 0:
            self.body = placeholder_text("You have no notes yet, to create "
                "a note type a note title then press Enter")
        else:
            self.body = urwid.Padding(self.list_box, left=1, right=1)

        matching_notes = self.notebook.search(query)

        matching_notes.sort(key=lambda x: x.mtime, reverse=True)

        self.list_box.filter(matching_notes)

        autocompletable_matches = []
        if query:
            for note in matching_notes:
                if note.title.lower().startswith(query.lower()):
                    autocompletable_matches.append(note)

        if autocompletable_matches:
            self.selected_note = autocompletable_matches[0]
        else:
            self.selected_note = None

    def on_search_box_changed(self, edit, new_edit_text):
        self.filter(new_edit_text)

    def on_list_box_changed(self, note):
        self.selected_note = note


def launch(notes_dir, editor, extension, extensions, exclude=None):
    """Launch the user interface."""

    urwid.set_encoding(sys.getfilesystemencoding())

    frame = MainFrame(notes_dir, editor, extension, extensions, exclude=exclude)
    loop = urwid.MainLoop(frame, palette)
    frame.loop = loop
    loop.run()
