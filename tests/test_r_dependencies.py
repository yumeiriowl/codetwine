"""R files depend on each other through the names they see without an import statement.

A name is looked up in the file itself, the box modules box::use attaches, the scripts
read with source(), global.R and the R directory of a Shiny app, the helper scripts of a
testthat directory, the package the file is in and the project packages attached with
library(). build_project_dependencies() adds an edge for each name that leads into
another file, and get_file_dependencies() lists each usage with the definition it names.
"""

import os

import pytest

from codetwine.extractors import dependency_graph
from codetwine.extractors.definition_source import extract_callee_source
from codetwine.extractors.dependency_graph import (
    build_project_dependencies,
)
from codetwine.extractors.definitions import extract_definitions
from codetwine.config.settings import EXT_TO_DEFINITION_DICT, has_language
from codetwine.file_analyzer import get_file_dependencies
from codetwine.parsers.r_markdown import r_chunk_code
from codetwine.parsers.ts_parser import parse_cache_dict, parse_file
from codetwine.r_name_index import r_name_index_cache_dict, r_target_cache_dict
from codetwine.utils.file_utils import copy_path_to_rel

PACKAGE_FILE_DICT = {
    "DESCRIPTION": "Package: calc\nVersion: 0.1\n",
    "R/add.R": """\
MAX_N <- 10L

add <- function(x, y = MAX_N) {
  x + y
}

`%+%` <- function(a, b) add(a, b)
""",
    "R/stats.R": """\
total <- function(xs, n = 2) {
  add <- function(a) a
  out <- 0
  for (x in xs) out <- out %+% add(x)
  scale_by(out, n)
}

scale_by <- function(value, n) value * n

summarise_all <- function(xs) lapply(xs, add)

describe <- function(x, ...) UseMethod("describe")

describe.default <- function(x, ...) add(x, 1)

describe.data.frame <- function(x, ...) NULL
""",
    "R/person.R": """\
setClass("Person", representation(name = "character"))

setGeneric("greet", function(obj, ...) standardGeneric("greet"))

setMethod("greet", "Person", function(obj, ...) add(1, 2))

Account <- R6::R6Class("Account",
  public = list(
    balance = 0,
    deposit = function(x) {
      self$balance <- add(self$balance, x)
      invisible(self)
    }
  ),
  private = list(secret = function() 42)
)

make_person <- function(name) new("Person", name = name)
""",
    "R/student.R": """\
setClass("Student", contains = "Person")

setMethod("greet", signature("Student"), function(obj, ...) NULL)

open_account <- function() Account$new()

runner <- function(xs) do.call("total", list(xs))

fallback <- function(x) describe.default(x)
""",
    "tests/testthat/helper-data.R": "sample_xs <- c(1, 2, 3)\n",
    "tests/testthat/test-add.R": """\
test_that("add works", {
  total <- add(1, 2)
  expect_equal(total, 3)
  expect_equal(calc::scale_by(sample_xs, 2), calc:::add(1, 1))
})
""",
    "vignettes/intro.Rmd": """\
---
title: Intro
---

Text with add(1, 2) in prose.

```{r setup}
library(calc)
res <- add(1, 2)
```

```{python}
total(1)
```
""",
    "vignettes/report.qmd": """\
# Report

~~~{r}
#| label: scale
scaled <- scale_by(3, 2)
~~~
""",
}

SCRIPT_FILE_DICT = {
    "main.R": """\
source("R/helper.R")
source(file.path("R", "io.R"))
source(paste0(base_dir, "/skip.R"))
result <- clean(load_data("x.csv"))
""",
    "R/helper.R": "source(\"util.R\")\nclean <- function(df) trim(df)\n",
    "R/util.R": "trim <- function(x) x\n",
    "R/io.R": "load_data <- function(path) read.csv(path)\n",
    "R/skip.R": "clean <- function(df) NULL\n",
    "other.r": "value <- clean(1)\n",
}

SHINY_FILE_DICT = {
    "app1/global.R": "choices <- c(\"a\", \"b\")\n",
    "app1/ui.R": "ui <- fluidPage(selectInput(\"x\", \"X\", choices), plot_ui(\"p\"))\n",
    "app1/server.R": "server <- function(input, output) {\n  output$p <- render_plot(choices)\n}\n",
    "app1/R/plot.R": "plot_ui <- function(id) id\nrender_plot <- function(x) x\n",
    "app2/app.R": "ui <- 1\nserver <- function(input, output) plot_ui(\"q\")\n",
    "lib/tool.R": "x <- choices\n",
}

BOX_FILE_DICT = {
    "app/main.R": """\
box::use(
  app/logic/calc[add, minus = sub],
  app/logic/format,
  fmt = ./view/table,
  ./view/chart[...],
  shiny[div],
)

run <- function() {
  add(1, minus(2, 1))
  format$as_text(1)
  fmt$render(draw())
  div()
}
""",
    "app/logic/calc.R": "add <- function(a, b) a + b\nsub <- function(a, b) a - b\n",
    "app/logic/format.R": "as_text <- function(x) as.character(x)\n",
    "app/view/table.R": "render <- function(x) x\n",
    "app/view/chart/__init__.R": "draw <- function() 1\n",
    "app/view/unused.R": "box::use(app/logic/calc)\n",
}

LIBRARY_FILE_DICT = {
    "alpha/DESCRIPTION": "Type: Package\nPackage: alpha\n",
    "alpha/R/x.R": "x_fn <- function() 1\n",
    "demo/DESCRIPTION": "Title: A Shiny demo\nType: Shiny\n",
    "demo/run.R": "library(alpha)\nx_fn()\n",
    "demo/other.R": "x_fn()\n",
    "demo/qualified.R": "alpha::x_fn()\n",
}


@pytest.fixture(autouse=True)
def _exclude_only_git(monkeypatch):
    """Analyze the tests directory of the projects whatever EXCLUDE_PATTERNS is set to."""
    monkeypatch.setattr(dependency_graph, "EXCLUDE_PATTERNS", [".git"])


def _make_files(tmp_path, file_dict: dict[str, str], name: str = "rapp") -> str:
    """Create a project directory holding the given files."""
    project = tmp_path / name
    for file_rel, text in file_dict.items():
        path = project / file_rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    parse_cache_dict.clear()
    r_name_index_cache_dict.clear()
    r_target_cache_dict.clear()
    return str(project)


def _graph(project_dir: str) -> dict[str, dict]:
    """Build the dependency graph keyed by the original relative path."""
    project_name = os.path.basename(project_dir)
    graph = {}
    for dep in build_project_dependencies(project_dir):
        rel = copy_path_to_rel(dep["file"][len(project_name) + 1:])
        graph[rel] = {
            key: {copy_path_to_rel(p[len(project_name) + 1:]) for p in dep[key]}
            for key in ("callers", "callees")
        }
    return graph


def _file_dependencies(project_dir: str, file_rel: str) -> dict:
    """Run get_file_dependencies for one file of the project."""
    graph = _graph(project_dir)
    project_file_set = {f for f in graph if has_language(os.path.join(project_dir, f))}
    caller_map = {f: sorted(graph[f]["callers"]) for f in project_file_set}
    return get_file_dependencies(
        os.path.join(project_dir, file_rel), project_dir,
        project_file_set, caller_map,
    )


def _usage_dict(result: dict, key: str = "callee_usages") -> dict[tuple[str, str], list[int]]:
    """Return {(name, file): lines} of a usage list of a get_file_dependencies result."""
    file_key = "from" if key == "callee_usages" else "file"
    return {(usage["name"], usage[file_key]): usage["lines"] for usage in result[key]}


def test_package_edges(tmp_path):
    graph = _graph(_make_files(tmp_path, PACKAGE_FILE_DICT))

    assert graph["R/add.R"]["callees"] == set()
    assert graph["R/stats.R"]["callees"] == {"R/add.R"}
    assert graph["R/person.R"]["callees"] == {"R/add.R"}
    assert graph["R/student.R"]["callees"] == {"R/person.R", "R/stats.R"}
    # The names of the package, of the helper script and of calc:: / calc:::
    assert graph["tests/testthat/test-add.R"]["callees"] == {
        "R/add.R", "R/stats.R", "tests/testthat/helper-data.R",
    }
    assert graph["vignettes/intro.Rmd"]["callees"] == {"R/add.R"}
    assert graph["vignettes/report.qmd"]["callees"] == {"R/stats.R"}
    assert graph["DESCRIPTION"]["callees"] == set()


def test_definitions(tmp_path):
    project_dir = _make_files(tmp_path, PACKAGE_FILE_DICT)
    root = parse_file(os.path.join(project_dir, "R/person.R"))[0]
    definition_list = [
        (d.name, d.type, d.start_line, d.end_line)
        for d in extract_definitions(root, EXT_TO_DEFINITION_DICT["r"])
    ]

    assert definition_list == [
        ("Person", "call", 1, 1),
        ("greet", "call", 3, 3),
        ("greet", "call", 5, 5),
        ("Account", "binary_operator", 7, 16),
        ("balance", "argument", 9, 9),
        ("deposit", "argument", 10, 13),
        ("secret", "argument", 15, 15),
        ("make_person", "function_definition", 18, 18),
    ]


def test_definition_forms(tmp_path):
    project_dir = _make_files(tmp_path, {"forms.R": """\
a = 1
b <<- 2
3 -> c
4 ->> d
`my var` <- 5
"strfn" <- function() NULL
if (TRUE) {
  inner <- function() {
    local_name <- 1
  }
}
x$field <- 1
names(a) <- "n"
Gen <- setClass("Shape", representation(n = "numeric"))
Stack <- setRefClass("Stack", fields = list(items = "list"),
  methods = list(push = function(x) x))
"""})
    root = parse_file(os.path.join(project_dir, "forms.R"))[0]
    definition_list = [
        (d.name, d.type) for d in extract_definitions(root, EXT_TO_DEFINITION_DICT["r"])
    ]

    assert definition_list == [
        ("a", "binary_operator"),
        ("b", "binary_operator"),
        ("c", "binary_operator"),
        ("d", "binary_operator"),
        ("my var", "binary_operator"),
        ("strfn", "function_definition"),
        ("inner", "function_definition"),
        ("Gen", "binary_operator"),
        ("Shape", "call"),
        ("Stack", "binary_operator"),
        ("items", "argument"),
        ("push", "argument"),
    ]


def test_callee_usages(tmp_path):
    project_dir = _make_files(tmp_path, PACKAGE_FILE_DICT)
    result = _file_dependencies(project_dir, "R/student.R")

    assert result["language"] == "r"
    # A class named by contains, a generic named by setMethod, a class used with $new,
    # a function named by do.call, and a name with dots
    assert _usage_dict(result) == {
        ("Person", "R/person.R"): [1],
        ("greet", "R/person.R"): [3],
        ("Account", "R/person.R"): [5],
        ("total", "R/stats.R"): [7],
        ("describe.default", "R/stats.R"): [9],
    }
    context_dict = {usage["name"]: usage["target_context"] for usage in result["callee_usages"]}
    assert context_dict["describe.default"] == "describe.default <- function(x, ...) add(x, 1)"
    assert context_dict["Account"].startswith("Account <- R6::R6Class(")
    assert context_dict["Account"].endswith(")")
    assert context_dict["Person"] == 'setClass("Person", representation(name = "character"))'


def test_local_names_are_not_usages(tmp_path):
    project_dir = _make_files(tmp_path, PACKAGE_FILE_DICT)

    # add is a function of total itself on line 4; a parameter default and a member
    stats_result = _file_dependencies(project_dir, "R/stats.R")
    assert _usage_dict(stats_result) == {
        ("%+%", "R/add.R"): [4],
        ("add", "R/add.R"): [10, 14],
    }

    # total is assigned inside the block given to test_that
    test_result = _file_dependencies(project_dir, "tests/testthat/test-add.R")
    assert _usage_dict(test_result) == {
        ("add", "R/add.R"): [2, 4],
        ("scale_by", "R/stats.R"): [4],
        ("sample_xs", "tests/testthat/helper-data.R"): [4],
    }


def test_same_file_usages(tmp_path):
    project_dir = _make_files(tmp_path, PACKAGE_FILE_DICT)

    person_result = _file_dependencies(project_dir, "R/person.R")
    assert {u["name"]: u["lines"] for u in person_result["same_file_usages"]} == {
        "Person": [5, 18],
        "greet": [5],
    }

    # An S3 method leads to its generic on the line the method starts
    stats_result = _file_dependencies(project_dir, "R/stats.R")
    assert {u["name"]: u["lines"] for u in stats_result["same_file_usages"]} == {
        "scale_by": [5],
        "describe": [14, 16],
    }

    add_result = _file_dependencies(project_dir, "R/add.R")
    assert {u["name"]: u["lines"] for u in add_result["same_file_usages"]} == {
        "MAX_N": [3],
        "add": [7],
    }


def test_caller_usages(tmp_path):
    project_dir = _make_files(tmp_path, PACKAGE_FILE_DICT)
    result = _file_dependencies(project_dir, "R/add.R")

    assert _usage_dict(result, "caller_usages") == {
        ("add", "R/person.R"): [5, 11],
        ("%+%", "R/stats.R"): [4],
        ("add", "R/stats.R"): [10, 14],
        ("add", "tests/testthat/test-add.R"): [2, 4],
        ("add", "vignettes/intro.Rmd"): [9],
    }
    context = next(
        u["usage_context"] for u in result["caller_usages"] if u["file"] == "vignettes/intro.Rmd"
    )
    assert "res <- add(1, 2)" in context


def test_caller_usages_of_a_name_defined_twice_in_a_file(tmp_path):
    project_dir = _make_files(tmp_path, {
        "DESCRIPTION": "Package: demo\nVersion: 0.1\n",
        "R/a.R": "opts <- list(a = 1)\nopts <- modifyList(opts, list(b = 2))\n",
        "R/b.R": (
            "use_opts <- function() {\n  print(opts)\n  x <- 1\n"
            "  y <- 2\n  str(opts)\n}\n"
        ),
    })
    caller_list = _file_dependencies(project_dir, "R/a.R")["caller_usages"]

    # One entry per caller and name, whatever definition of the name each line leads to
    assert [(u["name"], u["file"], u["lines"]) for u in caller_list] == [
        ("opts", "R/b.R", [2, 5]),
    ]


def test_usage_context_gives_the_code_around_near_lines_once(tmp_path):
    project_dir = _make_files(tmp_path, {
        "DESCRIPTION": "Package: demo\nVersion: 0.1\n",
        "R/a.R": "opts <- list(a = 1)\n",
        "R/b.R": (
            "use_opts <- function() {\n  print(opts)\n  x <- 1\n"
            "  y <- 2\n  str(opts)\n}\n"
        ),
    })
    caller_list = _file_dependencies(project_dir, "R/a.R")["caller_usages"]

    context_line_list = caller_list[0]["usage_context"].split("\n")
    assert context_line_list == [
        "use_opts <- function() {", "  print(opts)", "  x <- 1", "  y <- 2", "  str(opts)", "}",
    ]


def test_s3_method_of_a_generic_in_another_file(tmp_path):
    project_dir = _make_files(tmp_path, {
        "DESCRIPTION": "Package: gen\n",
        "R/generic.R": "as.shape <- function(x, ...) UseMethod(\"as.shape\")\nplain <- function(x) x\n",
        "R/method.R": "as.shape.data.frame <- function(x, ...) x\n\nplain.thing <- function(x) x\n",
    })
    result = _file_dependencies(project_dir, "R/method.R")

    # plain does not call UseMethod
    assert _usage_dict(result) == {("as.shape", "R/generic.R"): [1]}


def test_r_markdown_and_quarto_chunks(tmp_path):
    project_dir = _make_files(tmp_path, PACKAGE_FILE_DICT)

    rmd_result = _file_dependencies(project_dir, "vignettes/intro.Rmd")
    assert rmd_result["language"] == "rmd"
    assert [(d["name"], d["start_line"]) for d in rmd_result["definitions"]] == [("res", 9)]
    assert rmd_result["definitions"][0]["context"] == "res <- add(1, 2)"
    # The prose and the python chunk are not read
    assert _usage_dict(rmd_result) == {("add", "R/add.R"): [9]}

    qmd_result = _file_dependencies(project_dir, "vignettes/report.qmd")
    assert qmd_result["language"] == "qmd"
    assert _usage_dict(qmd_result) == {("scale_by", "R/stats.R"): [5]}


def test_r_chunk_code_keeps_positions():
    content = (
        "# Title é\n```{r, echo=FALSE}\nx <- 1\n```\ntext\n"
        "  ````{r name}\n  y <- 2\n  ```\n  z <- 3\n  ````\n```{rcpp}\nint a;\n```\n"
    ).encode("utf-8")
    code = r_chunk_code(content)

    assert len(code) == len(content)
    assert code.count(b"\n") == content.count(b"\n")
    line_list = code.decode("utf-8").split("\n")
    assert [line.strip() for line in line_list] == [
        "", "", "x <- 1", "", "", "", "y <- 2", "```", "z <- 3", "", "", "", "", "",
    ]


def test_source(tmp_path):
    project_dir = _make_files(tmp_path, SCRIPT_FILE_DICT)
    graph = _graph(project_dir)

    # A path from the project root, file.path() of literals; a computed path is not followed
    assert graph["main.R"]["callees"] == {"R/helper.R", "R/io.R"}
    # A path from the directory of the file
    assert graph["R/helper.R"]["callees"] == {"R/util.R"}
    # A script that reads nothing sees no other script
    assert graph["other.r"]["callees"] == set()
    assert graph["R/skip.R"]["callers"] == set()

    result = _file_dependencies(project_dir, "main.R")
    assert _usage_dict(result) == {
        ("clean", "R/helper.R"): [4],
        ("load_data", "R/io.R"): [4],
    }
    assert _file_dependencies(project_dir, "other.r")["language"] == "r"


def test_shiny_app(tmp_path):
    project_dir = _make_files(tmp_path, SHINY_FILE_DICT)
    graph = _graph(project_dir)

    assert graph["app1/ui.R"]["callees"] == {"app1/global.R", "app1/R/plot.R"}
    assert graph["app1/server.R"]["callees"] == {"app1/global.R", "app1/R/plot.R"}
    # Another app and a script outside an app do not see them
    assert graph["app2/app.R"]["callees"] == set()
    assert graph["lib/tool.R"]["callees"] == set()

    result = _file_dependencies(project_dir, "app1/server.R")
    assert _usage_dict(result) == {
        ("choices", "app1/global.R"): [2],
        ("render_plot", "app1/R/plot.R"): [2],
    }


def test_box_use(tmp_path):
    project_dir = _make_files(tmp_path, BOX_FILE_DICT)
    graph = _graph(project_dir)

    assert graph["app/main.R"]["callees"] == {
        "app/logic/calc.R",
        "app/logic/format.R",
        "app/view/table.R",
        "app/view/chart/__init__.R",
    }
    # A module that is used by no name is a dependency all the same
    assert graph["app/view/unused.R"]["callees"] == {"app/logic/calc.R"}

    result = _file_dependencies(project_dir, "app/main.R")
    # A name attached under another name is named after its definition
    assert _usage_dict(result) == {
        ("add", "app/logic/calc.R"): [10],
        ("sub", "app/logic/calc.R"): [10],
        ("as_text", "app/logic/format.R"): [11],
        ("render", "app/view/table.R"): [12],
        ("draw", "app/view/chart/__init__.R"): [12],
    }


def test_library_of_a_project_package(tmp_path):
    project_dir = _make_files(tmp_path, LIBRARY_FILE_DICT)
    graph = _graph(project_dir)

    assert graph["demo/run.R"]["callees"] == {"alpha/R/x.R"}
    assert graph["demo/qualified.R"]["callees"] == {"alpha/R/x.R"}
    # A DESCRIPTION file without a Package field makes no package, and a script that
    # attaches nothing does not see the package
    assert graph["demo/other.R"]["callees"] == set()


def test_extract_callee_source_takes_a_name_with_dots_whole(tmp_path):
    project_dir = _make_files(tmp_path, PACKAGE_FILE_DICT)

    assert extract_callee_source("R/stats.R", "describe.default", project_dir) == (
        "describe.default <- function(x, ...) add(x, 1)"
    )
    assert extract_callee_source("R/stats.R", "describe.nothing", project_dir) is None
    # A top-level definition comes before the call written for the name
    assert extract_callee_source("R/person.R", "greet", project_dir).startswith("setGeneric(")


def test_every_name_of_a_chain_is_a_definition(tmp_path):
    project_dir = _make_files(tmp_path, {
        "DESCRIPTION": "Package: chain\n",
        "R/defs.R": """\
fmt <- function(x) UseMethod("fmt")
setalloccol = alloc.col = function(DT) DT
A <- B <- 0
fmt.a <- fmt.b <- function(x) x
5 -> five -> also_five
""",
        "R/use.R": "u1 <- function(DT) alloc.col(DT)\nu2 <- function() B + five\n",
    })
    root = parse_file(os.path.join(project_dir, "R/defs.R"))[0]
    definition_list = [
        (d.name, d.type) for d in extract_definitions(root, EXT_TO_DEFINITION_DICT["r"])
    ]

    assert definition_list == [
        ("fmt", "function_definition"),
        ("setalloccol", "function_definition"),
        ("alloc.col", "function_definition"),
        ("A", "binary_operator"),
        ("B", "binary_operator"),
        ("fmt.a", "function_definition"),
        ("fmt.b", "function_definition"),
        ("also_five", "binary_operator"),
        ("five", "binary_operator"),
    ]
    assert _usage_dict(_file_dependencies(project_dir, "R/use.R")) == {
        ("alloc.col", "R/defs.R"): [1],
        ("B", "R/defs.R"): [2],
        ("five", "R/defs.R"): [2],
    }
    # Both methods of the chain lead to the generic
    same_dict = {
        u["name"]: u["lines"]
        for u in _file_dependencies(project_dir, "R/defs.R")["same_file_usages"]
    }
    assert same_dict["fmt"] == [4]


def test_replacement_function(tmp_path):
    project_dir = _make_files(tmp_path, {
        "DESCRIPTION": "Package: repl\n",
        "R/defs.R": """\
label <- function(x) attr(x, "label")
`label<-` <- function(x, value) x
"unit<-" <- function(x, value) x
setGeneric("name", function(x) standardGeneric("name"))
setGeneric("name<-", function(x, value) standardGeneric("name<-"))
""",
        "R/use.R": """\
f <- function(p) {
  label(p) <- "L"
  unit(p) <- 3
  repl::label(p) <- "M"
  label(p)
}
setReplaceMethod("name", "character", function(x, value) x)
""",
    })
    result = _file_dependencies(project_dir, "R/use.R")

    assert _usage_dict(result) == {
        ("label<-", "R/defs.R"): [2, 4],
        ("unit<-", "R/defs.R"): [3],
        ("label", "R/defs.R"): [5],
        ("name<-", "R/defs.R"): [7],
    }
    assert ("name<-", "call") in [(d["name"], d["type"]) for d in result["definitions"]]


def test_assignment_to_a_name_outside_the_function(tmp_path):
    project_dir = _make_files(tmp_path, {
        "main.R": "source(\"state.R\")\nreset <- function() {\n  state <<- 0\n  1 ->> state\n}\n",
        "state.R": "state <- 1\n",
        "closure.R": """\
source("state.R")
counter <- function() {
  state <- 0
  function() state <<- state + 1
}
""",
    })

    assert _usage_dict(_file_dependencies(project_dir, "main.R")) == {("state", "state.R"): [3, 4]}
    # The name is one of the function around the assignment
    assert _usage_dict(_file_dependencies(project_dir, "closure.R")) == {}


def test_defining_call_inside_another_call(tmp_path):
    project_dir = _make_files(tmp_path, {
        "DESCRIPTION": "Package: wrap\n",
        "R/defs.R": """\
suppressMessages(setGeneric("quiet", function(x) standardGeneric("quiet")))
invisible(setClass("Hidden", representation()))
run <- function() setClass("Local", representation())
""",
        "R/use.R": "use <- function(x) quiet(new(\"Hidden\"))\n",
    })
    root = parse_file(os.path.join(project_dir, "R/defs.R"))[0]
    definition_list = [
        (d.name, d.type) for d in extract_definitions(root, EXT_TO_DEFINITION_DICT["r"])
    ]

    assert definition_list == [("quiet", "call"), ("Hidden", "call"), ("run", "function_definition")]
    assert _usage_dict(_file_dependencies(project_dir, "R/use.R")) == {
        ("quiet", "R/defs.R"): [1],
        ("Hidden", "R/defs.R"): [1],
    }


def test_class_names_in_strings(tmp_path):
    project_dir = _make_files(tmp_path, {
        "DESCRIPTION": "Package: s4\n",
        "R/classes.R": """\
setClass("A", representation(n = "numeric"))
setClass("B", representation(n = "numeric"))
setGeneric("area", function(shape, other) standardGeneric("area"))
""",
        "R/use.R": """\
setMethod(f = "area", "A", function(shape, other) 1)
setMethod("area", definition = function(shape, other) 2, signature = c("A", "B"))
sig <- signature("B")
setClass("C", slots = c(inner = "A"))
setClass("D", representation(first = "B"))
setRefClass("E", fields = list(item = "A"), contains = "B")
setClassUnion("AorB", members = c("A", "B"))
""",
    })
    result = _file_dependencies(project_dir, "R/use.R")

    assert _usage_dict(result) == {
        ("area", "R/classes.R"): [1, 2],
        ("A", "R/classes.R"): [1, 2, 4, 6, 7],
        ("B", "R/classes.R"): [2, 3, 5, 6, 7],
    }


def test_scripts_read_together_see_each_other(tmp_path):
    project_dir = _make_files(tmp_path, {
        "main.R": "limit <- 3\nsource(\"R/helper.R\")\nsource(\"R/io.R\")\n",
        "R/helper.R": "clean <- function(x) head(x, limit)\n",
        "R/io.R": "load_clean <- function(path) clean(read.csv(path))\n",
        "R/alone.R": "other <- function(x) clean(x)\n",
    })
    graph = _graph(project_dir)

    # A script sees the names of the script that reads it and of the scripts that one reads
    assert graph["R/io.R"]["callees"] == {"R/helper.R"}
    assert graph["R/helper.R"]["callees"] == {"main.R"}
    assert graph["R/alone.R"]["callees"] == set()


def test_what_global_and_helper_scripts_read_and_attach(tmp_path):
    project_dir = _make_files(tmp_path, {
        "mypkg/DESCRIPTION": "﻿Package: mypkg\n",
        "mypkg/R/fn.R": "pkg_fn <- function() 1\n",
        "app/global.R": "library(mypkg)\nsource(\"helpers.R\")\ngval <- 1\n",
        "app/helpers.R": "h <- function() gval\n",
        "app/server.R": "server <- function(input, output) h() + gval + pkg_fn()\n",
        "checks/testthat/helper.R": "library(mypkg)\nsource(\"fixtures/fx.R\")\n",
        "checks/testthat/fixtures/fx.R": "fixture <- function() 1\n",
        "checks/testthat/test-a.R": "test_that(\"a\", {\n  expect_equal(fixture(), pkg_fn())\n})\n",
    })
    graph = _graph(project_dir)

    assert graph["app/server.R"]["callees"] == {"app/global.R", "app/helpers.R", "mypkg/R/fn.R"}
    assert graph["app/helpers.R"]["callees"] == {"app/global.R"}
    assert graph["checks/testthat/test-a.R"]["callees"] == {
        "checks/testthat/fixtures/fx.R", "mypkg/R/fn.R",
    }


def test_box_module_that_binds_and_attaches_other_modules(tmp_path):
    project_dir = _make_files(tmp_path, {
        "app/main.R": """\
box::use(app/logic, app/view)

run <- function() {
  logic$data$load()
  view$render()
  logic$own()
}
""",
        "app/logic/__init__.R": "box::use(app/logic/data)\nown <- function() 1\n",
        "app/logic/data.R": "load <- function() 1\n",
        "app/view/__init__.R": "box::use(./table[render])\n",
        "app/view/table.R": "render <- function() 1\n",
    })
    result = _file_dependencies(project_dir, "app/main.R")

    assert _usage_dict(result) == {
        ("load", "app/logic/data.R"): [4],
        ("render", "app/view/table.R"): [5],
        ("own", "app/logic/__init__.R"): [6],
    }


def test_r_chunk_header_forms():
    content = (
        b"```{r, fig.cap='a {b} c'}\nx <- 1\n```\n"
        b"> ```{r}\n> y <- 2\n> ```\n"
        b"```{rcpp}\nint a;\n```\n"
    )
    code = r_chunk_code(content)

    assert len(code) == len(content)
    assert [line.strip() for line in code.decode("utf-8").split("\n")] == [
        "", "x <- 1", "", "", "y <- 2", "", "", "", "", "",
    ]
    assert code.split(b"\n")[4] == b"  y <- 2"


def test_a_chunk_written_inside_a_longer_fence_is_a_chunk():
    content = (
        b"```{r}\nx <- 1\n```\n"                           # 1-3
        b"````markdown\n```{r}\ny <- 2\n```\n````\n"       # 4-8
        b"~~~~\n```{r}\nz <- 3\n```\n~~~~\n"               # 9-13
        b"    ```{r}\n    w <- 4\n    ```\n"               # 14-16
    )
    code = r_chunk_code(content)

    assert len(code) == len(content)
    assert [line.strip() for line in code.decode("utf-8").split("\n")] == [
        "", "x <- 1", "", "", "", "y <- 2", "", "", "", "", "z <- 3", "", "", "", "w <- 4", "", "",
    ]


def test_long_chain_of_operators(tmp_path):
    chain = " + ".join(f"t{i}" for i in range(3000))
    project_dir = _make_files(tmp_path, {
        "DESCRIPTION": "Package: deep\n",
        "R/deep.R": f"deep_def <- function() 1\ntotal <- {chain}\n",
        "R/use.R": "use <- function() deep_def()\n",
    })

    assert _usage_dict(_file_dependencies(project_dir, "R/use.R")) == {
        ("deep_def", "R/deep.R"): [1],
    }


def test_package_added_between_two_analyses(tmp_path):
    file_dict = {"R/a.R": "a_fn <- function() 1\n", "R/b.R": "b_fn <- function() a_fn()\n"}
    project_dir = _make_files(tmp_path, file_dict)
    assert _graph(project_dir)["R/b.R"]["callees"] == set()

    with open(os.path.join(project_dir, "DESCRIPTION"), "w") as description_file:
        description_file.write("Package: late\n")
    parse_cache_dict.clear()
    assert _graph(project_dir)["R/b.R"]["callees"] == {"R/a.R"}


def test_document_without_an_r_chunk_has_no_language(tmp_path):
    project_dir = _make_files(tmp_path, {
        "DESCRIPTION": "Package: docs\n",
        "R/a.R": "a_fn <- function() 1\n",
        "notes.qmd": "# Notes\n\n```{python}\na_fn()\n```\n",
        "prose.Rmd": "Only text about a_fn().\n",
        "report.qmd": "```{r}\na_fn()\n```\n",
    })
    graph = _graph(project_dir)

    assert not has_language(os.path.join(project_dir, "notes.qmd"))
    assert not has_language(os.path.join(project_dir, "prose.Rmd"))
    assert has_language(os.path.join(project_dir, "report.qmd"))
    assert graph["notes.qmd"]["callees"] == set()
    assert graph["report.qmd"]["callees"] == {"R/a.R"}
    result = _file_dependencies(project_dir, "notes.qmd")
    assert result["language"] == ""
    assert result["definitions"] == []

    # A chunk written into the file gives it its language at the next analysis
    with open(os.path.join(project_dir, "prose.Rmd"), "a") as document_file:
        document_file.write("\n```{r}\na_fn()\n```\n")
    parse_cache_dict.clear()
    assert _graph(project_dir)["prose.Rmd"]["callees"] == {"R/a.R"}


def test_several_packages_of_one_name(tmp_path):
    project_dir = _make_files(tmp_path, {
        "v1/DESCRIPTION": "Package: same\n",
        "v1/R/x.R": "shared <- function() 1\nown_call <- function() same::shared()\n",
        "v2/DESCRIPTION": "Package: same\n",
        "v2/R/x.R": "shared <- function() 2\n",
        "use/qualified.R": "same::shared()\n",
        "use/attached.R": "library(same)\nshared()\n",
    })
    graph = _graph(project_dir)

    assert graph["use/qualified.R"]["callees"] == {"v1/R/x.R", "v2/R/x.R"}
    assert graph["use/attached.R"]["callees"] == {"v1/R/x.R", "v2/R/x.R"}
    # Inside one of the packages the name is the package itself
    assert graph["v1/R/x.R"]["callees"] == set()


def test_source_path_in_another_case(tmp_path):
    project_dir = _make_files(tmp_path, {
        "main.R": "source(\"r/Helper.r\")\nhelp_fn()\n",
        "R/helper.R": "help_fn <- function() 1\n",
        "two.R": "source(\"lib/x.r\")\nx_fn()\n",
        "lib/x.R": "x_fn <- function() 1\n",
        "lib/X.r": "x_fn <- function() 2\n",
        "exact.R": "source(\"lib/X.r\")\nx_fn()\n",
    })
    graph = _graph(project_dir)

    assert graph["main.R"]["callees"] == {"R/helper.R"}
    # Two scripts differ from the path only in case: neither is taken
    assert graph["two.R"]["callees"] == set()
    assert graph["exact.R"]["callees"] == {"lib/X.r"}


def test_package_script_read_by_another_script_sees_the_package_first(tmp_path):
    project_dir = _make_files(tmp_path, {
        "DESCRIPTION": "Package: p\n",
        "R/util.R": "run <- function() clean(1)\n",
        "R/clean.R": "clean <- function(x) x\n",
        "data-raw/build.R": "source(\"../R/util.R\")\nclean <- TRUE\nrun()\n",
    })
    graph = _graph(project_dir)

    assert graph["R/util.R"]["callees"] == {"R/clean.R"}
    # A script of the package directory outside R/ sees its own names, then the package
    assert graph["data-raw/build.R"]["callees"] == {"R/util.R"}


def test_slot_and_field_names_are_not_class_names(tmp_path):
    project_dir = _make_files(tmp_path, {
        "main.R": """\
source("names.R")
setClass("K", slots = c("data", "p"))
setRefClass("RC", fields = c("data", "q"))
setClass("L", slots = c(data = "Typed"))
""",
        "names.R": "data <- 1\np <- 2\nq <- 3\nsetClass(\"Typed\", representation())\n",
    })

    assert _usage_dict(_file_dependencies(project_dir, "main.R")) == {("Typed", "names.R"): [4]}


def test_top_level_loops(tmp_path):
    project_dir = _make_files(tmp_path, {
        "a.R": """\
source("c.R")
for (i in 1:3) {
  acc <- helper(i)
}
while (acc < 10) acc <- acc + 1
repeat {
  done <- TRUE
  break
}
use <- function() c(acc, done, i)
""",
        "c.R": "acc <- 1\ndone <- 2\ni <- 5\nhelper <- function(x) x\n",
    })
    result = _file_dependencies(project_dir, "a.R")

    assert [(d["name"], d["start_line"]) for d in result["definitions"]] == [
        ("acc", 3), ("acc", 5), ("done", 7), ("use", 10),
    ]
    # The names the loops assign, and the variable of the for loop, are the file's own
    assert _usage_dict(result) == {("helper", "c.R"): [3]}
    assert {u["name"]: u["lines"] for u in result["same_file_usages"]} == {
        "acc": [10], "done": [10],
    }


def test_column_assignment_with_a_call_on_the_left(tmp_path):
    project_dir = _make_files(tmp_path, {
        "main.R": "source(\"cols.R\")\nf <- function(DT) DT[, my_cols(x) := 1]\n",
        "cols.R": "my_cols <- function(x) x\n`my_cols<-` <- function(x, value) x\n",
    })

    assert _usage_dict(_file_dependencies(project_dir, "main.R")) == {("my_cols", "cols.R"): [2]}


def test_assignment_outside_a_function_that_binds_the_name(tmp_path):
    project_dir = _make_files(tmp_path, {
        "main.R": """\
source("state.R")
f <- function(counter) {
  counter <<- counter + 1
}
g <- function() {
  total <- 0
  total <<- 5
}
""",
        "state.R": "counter <- 0\ntotal <- 0\n",
    })

    assert _usage_dict(_file_dependencies(project_dir, "main.R")) == {
        ("counter", "state.R"): [3],
        ("total", "state.R"): [7],
    }


def test_argument_name_written_in_part(tmp_path):
    project_dir = _make_files(tmp_path, {
        "main.R": "source(\"base.R\")\nsetMethod(\"show\", sig = \"Base\", function(object) 1)\n",
        "base.R": "setClass(\"Base\", representation())\n",
    })

    assert _usage_dict(_file_dependencies(project_dir, "main.R")) == {("Base", "base.R"): [2]}


def test_box_modules_that_attach_one_another_in_a_circle(tmp_path):
    file_dict = {
        "a.R": "box::use(./b[...])\nfa <- function() 1\n",
        "b.R": "box::use(./c[...])\nfb <- function() 1\n",
        "c.R": "box::use(./a[...])\nfc <- function() 1\n",
    }
    for module in ("a", "b", "c"):
        file_dict[f"use_{module}.R"] = f"box::use(./{module}[...])\nfa() + fb() + fc()\n"
    project_dir = _make_files(tmp_path, file_dict)

    for module in ("a", "b", "c"):
        result = _file_dependencies(project_dir, f"use_{module}.R")
        assert _usage_dict(result) == {
            ("fa", "a.R"): [2], ("fb", "b.R"): [2], ("fc", "c.R"): [2],
        }


def test_code_that_is_not_run_where_it_is_written(tmp_path):
    project_dir = _make_files(tmp_path, {"defs.R": """\
later <- quote(setClass("Quoted", representation()))
fmt <- function(x) {
  inner <- function(y) UseMethod("other")
  x
}
fmt.thing <- function(x) x
"""})
    result = _file_dependencies(project_dir, "defs.R")

    assert [d["name"] for d in result["definitions"]] == ["later", "fmt", "fmt.thing"]
    # fmt does not call UseMethod itself
    assert result["same_file_usages"] == []


def test_replacement_call_inside_another_target(tmp_path):
    project_dir = _make_files(tmp_path, {
        "main.R": """\
source("fn.R")
f <- function(x, v) {
  tags(x)[2] <- v
  names(tags(x)) <- v
  tags(x)$a <- v
  x
}
""",
        "fn.R": "tags <- function(x) x\n`tags<-` <- function(x, value) x\n",
    })

    assert _usage_dict(_file_dependencies(project_dir, "main.R")) == {
        ("tags", "fn.R"): [3, 4, 5],
        ("tags<-", "fn.R"): [3, 4, 5],
    }


def test_document_whose_lines_end_in_a_lone_cr(tmp_path):
    project_dir = _make_files(tmp_path, {
        "DESCRIPTION": "Package: cr\n",
        "R/a.R": "a_fn <- function() 1\n",
    })
    with open(os.path.join(project_dir, "doc.Rmd"), "wb") as document_file:
        document_file.write(b"# Title\r```{r}\ra_fn()\r```\r")
    parse_cache_dict.clear()
    graph = _graph(project_dir)

    assert has_language(os.path.join(project_dir, "doc.Rmd"))
    assert graph["doc.Rmd"]["callees"] == {"R/a.R"}


def test_a_chunk_whose_code_does_not_end_is_left_out(tmp_path):
    project_dir = _make_files(tmp_path, {
        "h.R": "hf <- function(x) x\n",
        "brace.Rmd": (
            "```{r}\nsource(\"h.R\")\ne1 <- hf(1)\n```\n\n"
            "```{r, eval=FALSE}\nmy_fun <- function(x) {\n  # ... your code\n```\n\n"
            "```{r}\ne2 <- function(y) hf(y)\n```\n\n"
            "```{r}\nresult <- hf(2) %>%\n```\n\n"
            "```{r}\ne3 <- hf(3)\n```\n"
        ),
    })
    result = _file_dependencies(project_dir, "brace.Rmd")
    assert [(d["name"], d["start_line"], d["end_line"]) for d in result["definitions"]] == [
        ("e1", 3, 3), ("e2", 12, 12), ("e3", 20, 20),
    ]
    assert _usage_dict(result) == {("hf", "h.R"): [3, 12, 20]}


def test_a_script_read_by_an_app_script_sees_the_names_of_the_app(tmp_path):
    project_dir = _make_files(tmp_path, {
        "global.R": "choices <- c('a', 'b')\n",
        "R/fmt.R": "fmt <- function(x) x\n",
        "ui.R": "ui <- fluidPage(source('ui/tab.R', local = TRUE)$value)\n",
        "server.R": "server <- function(input, output) {\n  fmt(choices)\n}\n",
        "ui/tab.R": "tabPanel('t', selectInput('s', 'S', choices), fmt(1))\n",
    })
    result = _file_dependencies(project_dir, "ui/tab.R")
    assert _usage_dict(result) == {("choices", "global.R"): [1], ("fmt", "R/fmt.R"): [1]}


def test_a_script_read_by_a_document_sees_the_names_of_the_document(tmp_path):
    project_dir = _make_files(tmp_path, {
        "report.Rmd": "# Report\n\n```{r}\nthreshold <- 5\nsource('plots.R')\nmake_plot(1)\n```\n",
        "plots.R": "make_plot <- function(x) x > threshold\n",
    })
    assert _usage_dict(_file_dependencies(project_dir, "plots.R")) == {
        ("threshold", "report.Rmd"): [1],
    }
    assert _usage_dict(_file_dependencies(project_dir, "report.Rmd")) == {
        ("make_plot", "plots.R"): [6],
    }


def test_every_target_of_an_equal_sign_before_an_arrow_is_a_definition(tmp_path):
    project_dir = _make_files(tmp_path, {
        "top.R": "a = b <- 3\nprint(a + b)\n",
    })
    result = _file_dependencies(project_dir, "top.R")
    assert [d["name"] for d in result["definitions"]] == ["a", "b"]
    assert {u["name"]: u["lines"] for u in result["same_file_usages"]} == {"a": [2], "b": [2]}


def test_modules_that_use_one_another_in_layers_are_read_once(tmp_path):
    layer_count = 16
    file_dict = {"main.R": "box::use(./l0_a[...], ./l0_b[...])\nf0_a()\n"}
    for layer in range(layer_count):
        for side in ("a", "b"):
            use_line = (
                f"box::use(./l{layer + 1}_a[...], ./l{layer + 1}_b[...])\n"
                if layer + 1 < layer_count else ""
            )
            file_dict[f"l{layer}_{side}.R"] = f"{use_line}f{layer}_{side} <- function() 1\n"
    project_dir = _make_files(tmp_path, file_dict)
    result = _file_dependencies(project_dir, "main.R")
    assert _usage_dict(result) == {("f0_a", "l0_a.R"): [2]}


def test_a_name_assigned_in_the_arguments_of_a_top_level_call_is_no_definition(tmp_path):
    project_dir = _make_files(tmp_path, {
        "DESCRIPTION": "Package: geo\nVersion: 0.1\n",
        "R/shape.R": "make_shape <- function(n) list(n = n)\narea <- function(s) s$n * 2\n",
        "tests/testthat/helper-data.R": "shape <- make_shape(3)\nsize <- 1\nlimit <- 9\n",
        "tests/testthat/test-area.R": (
            "test_that('a new shape', {\n"                       # 1
            "  shape <- make_shape(5)\n"                         # 2
            "  expect_equal(area(shape), 10)\n"                  # 3
            "})\n"
            "describe('area', {\n"                               # 5
            "  it('doubles', { size <- 2; expect_equal(size, 2) })\n"
            "})\n"
            "local({ limit <- 3 })\n"                            # 8
            "withr::with_options(list(digits = 3), { area <- 0 })\n"
            "on.exit({ shape <- NULL })\n"                       # 10
            "tryCatch({ size <- 4 }, error = function(e) NULL)\n"
            "if ((n <- 2) > 0) print(n)\n"                       # 12
            "test_that('the helper shape', {\n"                  # 13
            "  expect_equal(area(shape), 6 * size * limit * n)\n"
            "})\n"
        ),
    })
    result = _file_dependencies(project_dir, "tests/testthat/test-area.R")
    # Only the assignment outside every call is a definition of the file
    assert [(d["name"], d["start_line"]) for d in result["definitions"]] == [("n", 12)]
    assert _usage_dict(result) == {
        ("make_shape", "R/shape.R"): [2],
        ("area", "R/shape.R"): [3, 14],
        ("shape", "tests/testthat/helper-data.R"): [14],
        ("size", "tests/testthat/helper-data.R"): [14],
        ("limit", "tests/testthat/helper-data.R"): [14],
    }
    assert {u["name"]: u["lines"] for u in result["same_file_usages"]} == {"n": [14]}


def test_a_top_level_statement_reads_a_name_of_the_file_after_it_is_assigned(tmp_path):
    project_dir = _make_files(tmp_path, {
        "load.R": "data <- read.csv('in.csv')\ncfg <- list(a = 1)\nhelper <- function() 1\n",
        "analysis.R": (
            "source('load.R')\n"                                 # 1
            "print(cfg$a)\n"                                     # 2
            "data <- subset(data, x > 0)\n"                      # 3
            "cfg <- modifyList(cfg,\n"                           # 4
            "  list(b = cfg$a))\n"                               # 5
            "summary(data)\n"                                    # 6
            "run <- function() helper() + cfg$b\n"               # 7
            "helper <- function() 2\n"                           # 8
            "cfg$b\n"                                            # 9
        ),
    })
    result = _file_dependencies(project_dir, "analysis.R")
    assert _usage_dict(result) == {
        ("cfg", "load.R"): [2, 4, 5],
        ("data", "load.R"): [3],
    }
    # A read after the assignment, and a read inside a function, see the file's names
    assert {u["name"]: u["lines"] for u in result["same_file_usages"]} == {
        "data": [6], "cfg": [7, 9], "helper": [7],
    }


def test_library_reads_the_package_by_the_value_of_character_only(tmp_path):
    file_dict = {"names.R": "pkg_name <- 'p4'\n"}
    for package in ("p1", "p2", "p3", "p4", "p5"):
        file_dict[f"{package}/DESCRIPTION"] = f"Package: {package}\n"
        file_dict[f"{package}/R/f.R"] = f"{package}_fn <- function() 1\n"
    file_dict["main.R"] = (
        "source('names.R')\n"
        "library(p1, character.only = FALSE)\n"
        "require(p2, character.only = F)\n"
        "library('p3', character.only = TRUE)\n"
        "library(pkg_name, character.only = T)\n"
        "other::library(p5)\n"
        "p1_fn() + p2_fn() + p3_fn() + p4_fn() + p5_fn()\n"
    )
    project_dir = _make_files(tmp_path, file_dict)

    assert _usage_dict(_file_dependencies(project_dir, "main.R")) == {
        ("pkg_name", "names.R"): [5],
        ("p1_fn", "p1/R/f.R"): [7],
        ("p2_fn", "p2/R/f.R"): [7],
        ("p3_fn", "p3/R/f.R"): [7],
    }


def test_a_name_read_before_the_function_assigns_it_is_the_outer_name(tmp_path):
    project_dir = _make_files(tmp_path, {
        "config.R": (
            "cfg <- list(a = 1)\nload_data <- function(path) path\nhelper <- function(x) x\n"
            "old <- NULL\n"
        ),
        "main.R": (
            "source('config.R')\n"
            "run <- function(extra) {\n"
            "  cfg <- modifyList(cfg, extra)\n"
            "  cfg$a\n"
            "}\n"
            "prep <- function(path) {\n"
            "  load_data <- load_data(path)\n"
            "  nrow(load_data)\n"
            "}\n"
            "late <- function() {\n"
            "  v <- helper(2)\n"
            "  if (TRUE) helper <- function(x) 0\n"
            "  helper(v)\n"
            "}\n"
            "nest <- function() {\n"
            "  inner <- function() cfg\n"
            "  cfg <- 2\n"
            "  inner()\n"
            "}\n"
            "tryCatch({\n"
            "  cfg <- modifyList(cfg, list(b = 2))\n"
            "  cfg$b\n"
            "}, error = function(e) NULL)\n"
            "keep <- function() {\n"
            "  on.exit(options(old))\n"
            "  old <- options(digits = 3)\n"
            "}\n"
        ),
    })

    # The code given to on.exit() sees every name the function assigns
    assert _usage_dict(_file_dependencies(project_dir, "main.R")) == {
        ("cfg", "config.R"): [3, 21],
        ("load_data", "config.R"): [7],
        ("helper", "config.R"): [11],
    }


def test_deferred_code_and_parameter_defaults_see_every_name_of_the_function(tmp_path):
    project_dir = _make_files(tmp_path, {
        "defs.R": "old <- list()\ndata <- data.frame(x = 1)\ncon <- NULL\nn <- 1\n",
        "main.R": (
            "source('defs.R')\n"                                 # 1
            "f_defer <- function() {\n"                          # 2
            "  withr::defer(options(old))\n"
            "  old <- options(digits = 3)\n"
            "}\n"
            "f_parent <- function() {\n"                         # 6
            "  defer_parent(close(con))\n"
            "  con <- file('x')\n"
            "}\n"
            "f_default <- function(x, k = nrow(data)) {\n"       # 10
            "  data <- as.data.frame(x)\n"
            "  k\n"
            "}\n"
            "f_exit <- function() {\n"                           # 14
            "  base::on.exit(close(con))\n"
            "  con <- file('y')\n"
            "}\n"
            "f_other <- function() {\n"                          # 18
            "  other::defer(print(n))\n"
            "  n <- 2\n"
            "}\n"
            "f_outer <- function(k = n) k\n"                     # 22
        ),
    })
    # A defer() of another package runs where it is written
    assert _usage_dict(_file_dependencies(project_dir, "main.R")) == {
        ("n", "defs.R"): [19, 22],
    }


def test_the_names_of_a_box_module_are_merged_once(tmp_path, monkeypatch):
    from codetwine import r_name_index

    merge_count_list = []
    merge_table = r_name_index._merge_table

    def _count_merge(table_list):
        merge_count_list[-1] += 1
        return merge_table(table_list)

    monkeypatch.setattr(r_name_index, "_merge_table", _count_merge)
    for count in (10, 20):
        file_dict = {"mod.R": "".join(f"f{i} <- function() {i}\n" for i in range(count))}
        for side in ("a", "b"):
            file_dict[f"{side}/DESCRIPTION"] = "Package: pk\n"
            file_dict[f"{side}/R/g.R"] = "".join(f"g{i} <- function() {i}\n" for i in range(count))
        file_dict["main.R"] = "box::use(./mod, pk)\n" + "".join(
            f"y{i} <- mod$f{i}() + pk$g{i}()\n" for i in range(count)
        )
        project_dir = _make_files(tmp_path, file_dict, name=f"box{count}")
        merge_count_list.append(0)
        r_name_index._get_name_index(project_dir, set(file_dict))
        merge_count_list[-1] = 0
        target_list = r_name_index.r_reference_target_list("main.R", set(file_dict), project_dir)
        assert len(target_list) == 3 * count

    assert merge_count_list[0] == merge_count_list[1]
