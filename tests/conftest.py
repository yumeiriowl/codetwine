"""Settings shared by every test.

litellm reads its model cost map from the copy it ships with instead of fetching it,
in the test process and in the processes the tests start.
"""

import os

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
