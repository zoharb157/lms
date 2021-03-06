from typing import List, Optional, Tuple

from loguru import logger
from werkzeug.datastructures import FileStorage

from lms.extractors.base import Extractor, File
from lms.lmsdb.models import Exercise, Solution, User
from lms.lmstests.public.identical_tests import tasks as identical_tests_tasks
from lms.lmstests.public.flake8 import tasks as flake8_tasks
from lms.lmstests.public.unittests import tasks as unittests_tasks
from lms.models.errors import AlreadyExists, UploadError
from lms.lmsweb import config
from lms.utils import hashing


def _is_uploaded_before(user: User, file_hash: str) -> bool:
    return Solution.is_duplicate(file_hash, user, already_hashed=True)


def _upload_to_db(
        exercise_id: int,
        user: User,
        files: List[File],
        solution_hash: Optional[str] = None,
) -> Solution:
    exercise = Exercise.get_or_none(exercise_id)
    if exercise is None:
        raise UploadError(f'No such exercise id: {exercise_id}')
    elif not exercise.open_for_new_solutions():
        raise UploadError(
            f'Exercise {exercise_id} is closed for new solutions.')
    elif not files:
        raise UploadError(f'There are no files to upload for {exercise_id}.')

    return Solution.create_solution(
        exercise=exercise,
        solver=user,
        files=files,
        hash_=solution_hash,
    )


def _run_auto_checks(solution: Solution) -> None:
    flake8_tasks.run_flake8_on_solution.apply_async(args=(solution.id,))
    unittests_tasks.run_tests_for_solution.apply_async(args=(solution.id,))
    if config.FEATURE_FLAG_CHECK_IDENTICAL_CODE_ON:
        check_ident = identical_tests_tasks.solve_solution_with_identical_code
        check_ident.apply_async(args=(solution.id,))


def new(user: User, file: FileStorage) -> Tuple[List[int], List[int]]:
    solution_hash = hashing.by_file(file)
    if _is_uploaded_before(user, solution_hash):
        raise AlreadyExists('You try to reupload an old solution.')

    matches: List[int] = []
    misses: List[int] = []
    for exercise_id, files in Extractor(file):
        try:
            solution = _upload_to_db(exercise_id, user, files, solution_hash)
            _run_auto_checks(solution)
        except (UploadError, AlreadyExists) as e:
            logger.debug(e)
            misses.append(exercise_id)
        else:
            matches.append(exercise_id)

    return matches, misses
