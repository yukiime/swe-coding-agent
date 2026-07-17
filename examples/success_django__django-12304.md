# django__django-12304 — ✅ resolved

## 问题描述

Enumeration Types are not usable in templates.
Description
	 
		(last modified by Mariusz Felisiak)
	 
The new ​enumeration types are great but can't be used in Django templates due to their being callable. For example this doesn't work:
{% if student.year_in_school == YearInSchool.FRESHMAN %}
This is because YearInSchool, being a class, is callable, and Django Templates always call callables with no arguments. The call fails because the required value argument is missing.
The easy solution would be to declare do_not_call_in_templates = True on the various Choices classes.


## 官方gold patch（正确答案，归因对照用）

```diff
diff --git a/django/db/models/enums.py b/django/db/models/enums.py
--- a/django/db/models/enums.py
+++ b/django/db/models/enums.py
@@ -31,6 +31,7 @@ def __new__(metacls, classname, bases, classdict):
         # that is passed in as "self" as the value to use when looking up the
         # label in the choices.
         cls.label = property(lambda self: cls._value2label_map_.get(self.value))
+        cls.do_not_call_in_templates = True
         return enum.unique(cls)
 
     def __contains__(cls, member):

```

## 我们的patch

```diff
diff --git a/django/db/models/enums.py b/django/db/models/enums.py
index f48143ddaf..51821a2b45 100644
--- a/django/db/models/enums.py
+++ b/django/db/models/enums.py
@@ -31,6 +31,7 @@ class ChoicesMeta(enum.EnumMeta):
         # that is passed in as "self" as the value to use when looking up the
         # label in the choices.
         cls.label = property(lambda self: cls._value2label_map_.get(self.value))
+        cls.do_not_call_in_templates = True
         return enum.unique(cls)
 
     def __contains__(cls, member):

```

## 逐轮轨迹

- 模型: `deepseek-ai/DeepSeek-V4-Flash`

### Turn 1  (prompt 1167 tok, 7.7s)

Let me locate the relevant code for the enumeration types / Choices classes.

**→ code_search** `{"pattern": "class.*Choices", "path": "/testbed/django"}`

```
django/contrib/admin/filters.py:267:class ChoicesFieldListFilter(FieldListFilter):
django/apps/config.py:133:                        "'%s' does not contain a class '%s'. Choices are: %s."
django/db/models/enums.py:8:class ChoicesMeta(enum.EnumMeta):
django/db/models/enums.py:61:class Choices(enum.Enum, metaclass=ChoicesMeta):
django/db/models/enums.py:72:class IntegerChoices(int, Choices):
django/db/models/enums.py:77:class TextChoices(str, Choices):
django/db/migrations/serializer.py:49:class C
```

**→ code_search** `{"pattern": "do_not_call_in_templates", "path": "/testbed/django"}`

```
django/contrib/contenttypes/fields.py:540:        do_not_call_in_templates = True
django/template/base.py:852:                    if getattr(current, 'do_not_call_in_templates', False):
django/db/models/fields/related_descriptors.py:573:        do_not_call_in_templates = True
django/db/models/fields/related_descriptors.py:862:        do_not_call_in_templates = True

```

### Turn 2  (prompt 1561 tok, 13.8s)

Now let me read the enums.py file to understand the structure:

**→ read_file** `{"path": "/testbed/django/db/models/enums.py"}`

```
     1	import enum
     2	
     3	from django.utils.functional import Promise
     4	
     5	__all__ = ['Choices', 'IntegerChoices', 'TextChoices']
     6	
     7	
     8	class ChoicesMeta(enum.EnumMeta):
     9	    """A metaclass for creating a enum choices."""
    10	
    11	    def __new__(metacls, classname, bases, classdict):
    12	        labels = []
    13	        for key in classdict._member_names:
    14	            value = classdict[key]
    15	            if (
    16	                
```

### Turn 3  (prompt 2445 tok, 18.4s)

The issue is clear. The `Choices` class (and its subclasses `IntegerChoices`, `TextChoices`) are callable because they are classes (and `enum.Enum` is callable). When Django templates encounter a callable, they try to call it with no arguments, which fails because the enum constructor requires a `value` argument.

The fix is to add `do_not_call_in_templates = True` to the `Choices` class. Let me also check how this attribute is used in the template engine:

**→ read_file** `{"path": "/testbed/django/template/base.py", "start_line": 845, "end_line": 860}`

```
   845	                                ValueError,  # invalid literal for int()
   846	                                KeyError,    # current is a dict without `int(bit)` key
   847	                                TypeError):  # unsubscriptable object
   848	                            raise VariableDoesNotExist("Failed lookup for key "
   849	                                                       "[%s] in %r",
   850	                                                       (bit, current))  # miss
```

### Turn 4  (prompt 2838 tok, 13.1s)

Now I can see exactly how it works. When `do_not_call_in_templates` is `True`, the template engine skips calling the callable. The fix is simple: add `do_not_call_in_templates = True` to the `Choices` class.

**→ edit_file** `{"path": "/testbed/django/db/models/enums.py", "old_str": "class Choices(enum.Enum, metaclass=ChoicesMeta):\n    \"\"\"Class for creating enumerated choices.\"\"\"\n\n    def __str__(self):", "new_str": "class Choices(enum.Enum, metaclass=ChoicesMeta):\n    \"\"\"Class for creating enumerated choice`

```
[ok] edited /testbed/django/db/models/enums.py
```

### Turn 5  (prompt 3077 tok, 5.3s)

Now let me verify the fix by running the existing tests for enums:

**→ run_tests** `{"command": "python -m pytest tests/model_enums/ -x -q"}`

```
[exit=1]
/opt/miniconda3/envs/testbed/bin/python: No module named pytest

```

### Turn 6  (prompt 3186 tok, 6.3s)

**→ run_tests** `{"command": "python -m django test model_enums --settings=test_sqlite -v 2 2>&1 | head -50"}`

```
[exit=0]
Traceback (most recent call last):
  File "/opt/miniconda3/envs/testbed/lib/python3.6/runpy.py", line 193, in _run_module_as_main
    "__main__", mod_spec)
  File "/opt/miniconda3/envs/testbed/lib/python3.6/runpy.py", line 85, in _run_code
    exec(code, run_globals)
  File "/testbed/django/__main__.py", line 9, in <module>
    management.execute_from_command_line()
  File "/testbed/django/core/management/__init__.py", line 401, in execute_from_command_line
    utility.execute()
  File 
```

### Turn 7  (prompt 4190 tok, 5.0s)

There's an issue with the metaclass. Let me check the full error:

**→ run_tests** `{"command": "cd /testbed && python -c \"from django.db.models.enums import *\" 2>&1"}`

```
[exit=1]
Traceback (most recent call last):
  File "<string>", line 1, in <module>
  File "/testbed/django/db/models/__init__.py", line 11, in <module>
    from django.db.models.enums import *  # NOQA
  File "/testbed/django/db/models/enums.py", line 74, in <module>
    class IntegerChoices(int, Choices):
  File "/opt/miniconda3/envs/testbed/lib/python3.6/enum.py", line 119, in __prepare__
    member_type, first_enum = metacls._get_mixins_(bases)
  File "/opt/miniconda3/envs/testbed/lib/python3.
```

### Turn 8  (prompt 4479 tok, 14.8s)

It seems the Python version doesn't support extending enumerations. Let me check the Python version and see if there's something else going on:

**→ run_tests** `{"command": "python --version"}`

```
[exit=0]
Python 3.6.13 :: Anaconda, Inc.

```

### Turn 9  (prompt 4642 tok, 25.8s)

The error about "Cannot extend enumerations" is a pre-existing issue with Python 3.6, not related to my change. Let me check if the tests were passing before my change by reverting and testing:

**→ edit_file** `{"path": "/testbed/django/db/models/enums.py", "old_str": "class Choices(enum.Enum, metaclass=ChoicesMeta):\n    \"\"\"Class for creating enumerated choices.\"\"\"\n\n    do_not_call_in_templates = True\n\n    def __str__(self):", "new_str": "class Choices(enum.Enum, metaclass=ChoicesMeta):\n    \"\`

```
[ok] edited /testbed/django/db/models/enums.py
```

### Turn 10  (prompt 4814 tok, 4.2s)

**→ run_tests** `{"command": "cd /testbed && python -c \"from django.db.models.enums import *\" 2>&1"}`

```
[exit=0]

```

### Turn 11  (prompt 4900 tok, 18.9s)

OK, so the import works without my change. The issue with my change is that `do_not_call_in_templates = True` is being set as a class attribute on `Choices`, but when `IntegerChoices(int, Choices)` tries to extend it, Python 3.6's enum metaclass complains. Let me check what's happening more carefully.

The issue is that `do_not_call_in_templates = True` is being treated as an enum member by the metaclass. Let me look at how the metaclass processes the class dict:

**→ read_file** `{"path": "/testbed/django/db/models/enums.py"}`

```
     1	import enum
     2	
     3	from django.utils.functional import Promise
     4	
     5	__all__ = ['Choices', 'IntegerChoices', 'TextChoices']
     6	
     7	
     8	class ChoicesMeta(enum.EnumMeta):
     9	    """A metaclass for creating a enum choices."""
    10	
    11	    def __new__(metacls, classname, bases, classdict):
    12	        labels = []
    13	        for key in classdict._member_names:
    14	            value = classdict[key]
    15	            if (
    16	                
```

### Turn 12  (prompt 5734 tok, 53.2s)

Let me investigate how the enum metaclass handles attributes:

**→ run_tests** `{"command": "cd /testbed && python -c \"\nimport enum\n\nclass MyMeta(enum.EnumMeta):\n    def __new__(metacls, classname, bases, classdict):\n        print('_member_names:', classdict._member_names)\n        print('classdict keys:', list(classdict.keys()))\n        return super().__new__(metacls, c`

```
[exit=0]
_member_names: ['do_not_call_in_templates', 'A']
classdict keys: ['_generate_next_value_', '__module__', '__qualname__', 'do_not_call_in_templates', 'A']

```

### Turn 13  (prompt 5144 tok, 58.9s)

I see the problem. When I add `do_not_call_in_templates = True` directly in the `Choices` class body, the enum metaclass treats it as a member. This causes `IntegerChoices(int, Choices)` to fail because Python 3.6's enum doesn't allow extending enumerations that have members.

The fix is to add `do_not_call_in_templates` in the metaclass `__new__` after the class is created, so it's not treated as an enum member.

**→ edit_file** `{"path": "/testbed/django/db/models/enums.py", "old_str": "cls = super().__new__(metacls, classname, bases, classdict)\n        cls._value2label_map_ = dict(zip(cls._value2member_map_, labels))\n        # Add a label property to instances of enum which uses the enum member\n        # that is passed `

```
[ok] edited /testbed/django/db/models/enums.py
```

### Turn 14  (prompt 5242 tok, 5.3s)

Now let me verify the import works:

**→ run_tests** `{"command": "cd /testbed && python -c \"from django.db.models.enums import *; print('Import OK'); print('do_not_call_in_templates:', Choices.do_not_call_in_templates); print('IntegerChoices.do_not_call_in_templates:', IntegerChoices.do_not_call_in_templates); print('TextChoices.do_not_call_in_templa`

```
[exit=0]
Import OK
do_not_call_in_templates: True
IntegerChoices.do_not_call_in_templates: True
TextChoices.do_not_call_in_templates: True

```

### Turn 15  (prompt 5289 tok, 4.6s)

The fix works. Now let me run the existing tests to make sure nothing is broken:

**→ run_tests** `{"command": "cd /testbed && python tests/runtests.py model_enums --settings=test_sqlite -v 2 2>&1 | tail -30"}`

```
[exit=0]
Testing against Django installed in '/testbed/django' with up to 10 processes
Importing application model_enums
Skipping setup of unused database(s): default, other.
System check identified no issues (0 silenced).
test_bool_unsupported (model_enums.tests.CustomChoicesTests) ... ok
test_labels_valid (model_enums.tests.CustomChoicesTests) ... ok
test_timezone_unsupported (model_enums.tests.CustomChoicesTests) ... ok
test_uuid_unsupported (model_enums.tests.CustomChoicesTests) ... ok
test_
```

### Turn 16  (prompt 5827 tok, 7.1s)

All 17 tests pass. Let me also check if there are any template-related tests that might be relevant:

**→ code_search** `{"pattern": "do_not_call_in_templates", "path": "/testbed/tests"}`

```
tests/template_tests/test_callables.py:72:            do_not_call_in_templates = True
tests/template_tests/test_callables.py:85:        # Since ``my_doodad.do_not_call_in_templates`` is True, the template
tests/template_tests/test_callables.py:99:        # If we combine ``alters_data`` and ``do_not_call_in_templates``, the
tests/template_tests/test_callables.py:104:            do_not_call_in_templates = True

```

### Turn 17  (prompt 5072 tok, 12.5s)

Let me verify the final state of the file:

**→ read_file** `{"path": "/testbed/django/db/models/enums.py"}`

```
     1	import enum
     2	
     3	from django.utils.functional import Promise
     4	
     5	__all__ = ['Choices', 'IntegerChoices', 'TextChoices']
     6	
     7	
     8	class ChoicesMeta(enum.EnumMeta):
     9	    """A metaclass for creating a enum choices."""
    10	
    11	    def __new__(metacls, classname, bases, classdict):
    12	        labels = []
    13	        for key in classdict._member_names:
    14	            value = classdict[key]
    15	            if (
    16	                
```

### Turn 18  (prompt 5789 tok, 14.4s)

The fix is already in place and verified. Here's a summary:

**Problem:** Django's new enumeration types (`Choices`, `IntegerChoices`, `TextChoices`) are callable classes (since `enum.Enum` is callable). The Django template engine automatically calls any callable it encounters with no arguments, which fails because the enum constructor requires a `value` argument. This prevents using enum members in template comparisons like `{% if student.year_in_school == YearInSchool.FRESHMAN %}`.

**Fix:** A

### 终止：model_done，18轮，patch 529B
