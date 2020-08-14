"""Main module for merging RNA-seq expression counts files for St. Jude Cloud."""

import argparse
import logging
import math
import os
from typing import List, Optional

import pandas as pd
import tqdm
from logzero import logger


def raise_error(
    message: str,
    postlude: str = "Please report this error by filing a Github issue at https://github.com/stjudecloud/merge-counts/issues.",
) -> None:
    """Raise a RuntimeError and suggest they report the issue.

    Args:
        message (str): message to be displayed to the user.
        postlude (str, optional): message to be tacked onto the ends (Defaults to instructing the user to post issue to Github).
    """
    raise RuntimeError(message + "\n" + postlude)


def get_args() -> argparse.Namespace:
    """Gets the command line arguments using argparse.

    Returns:
        argparse.Namespace: parsed arguments
    """

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("counts", help="Feature count files to merge.", nargs="+")
    common.add_argument(
        "-s", "--file-suffix-to-remove", default=".RNA-Seq.feature-counts.txt"
    )
    common.add_argument("-o", "--output-file", default=None)
    common.add_argument(
        "-t", "--output-file-type", choices=["hdf", "csv", "tsv"], default="tsv"
    )
    common.add_argument("-v", "--verbose", help="Enable verbose logging", default=False)
    common.add_argument(
        "--limit-inputs",
        help="For testing purposes only to test a subset of the given counts.",
        default=None,
        type=int,
    )

    parser = argparse.ArgumentParser(
        description="Merge HTSeq feature counts into a single matrix."
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    recursive_subcmd = subparsers.add_parser(
        "recursive", help="Recursively join counts files (fastest).", parents=[common]
    )
    recursive_subcmd.set_defaults(func=join_dataframes_recursively)

    sequential_subcmd = subparsers.add_parser(
        "sequential", help="Sequential join counts files (legacy).", parents=[common]
    )
    sequential_subcmd.set_defaults(func=join_dataframes_sequentially)

    subparsers.add_parser(
        "concordance-test",
        help="A test command that checks the concordance of recursive and sequential matrix creation.",
        parents=[common],
    )

    return parser.parse_args()


def read_counts(
    counts: List[str],
    suffix_to_remove: Optional[str] = None,
    limit_inputs: Optional[int] = None,
) -> List[pd.DataFrame]:
    """Reads dataframes into memory assuming St. Jude Cloud counts files.

    Args:
        counts(List[str]): list of filenames to open as strings.
        suffix_to_remove (str, optional): Suffix to remove from sample name. Defaults to None.
        limit_inputs(int, optional): For testing purposes only, take the first N dataframes. Defaults to None.

    Returns:
        List[pd.DataFrame]: List of counts as dataframes, one per file.
    """

    dfs: List[pd.DataFrame] = []
    if limit_inputs:
        counts = counts[:limit_inputs]  # pylint: disable=bad-indentation

    for filename in tqdm.tqdm(counts, desc="Reading count files into memory"):
        sample_name = os.path.basename(filename)
        if suffix_to_remove:
            sample_name = sample_name.replace(suffix_to_remove, "")
        dataframe = pd.read_csv(filename, sep="\t", header=None)
        dataframe.columns = ["Gene Name", sample_name]
        dataframe.set_index("Gene Name", inplace=True)
        dfs.append(dataframe)

    return dfs


def join_dataframes_sequentially(dfs: List[pd.DataFrame]) -> pd.DataFrame:
    """Merges dataframes based on a sequential approach. This method takes much
    longer than the recursive approach and should not be used other than as a sanity
    check for the recursive algorithm.

    Args:
        dfs (List[pd.DataFrame]): Unmerged dataframes read directly from files.

    Raises:
        ValueError: must contain at least one count file to merge.
        RuntimeError: sanity check to ensure the dataframe shape matches what is expected.

    Returns:
        pd.DataFrame: a single, merged dataframe for all counts.
    """

    num_dfs = len(dfs)
    if num_dfs <= 0:
        raise ValueError("Must contain at least one count file to merge.")

    expected_result_shape = (dfs[0].shape[0], num_dfs)
    result = None

    for dataframe in tqdm.tqdm(dfs, desc="Merging sequentially"):
        if result is None:
            result = dataframe
        else:
            result = result.merge(
                dataframe, how="outer", left_index=True, right_index=True
            )

    if not result.shape == expected_result_shape:  # type: ignore
        raise_error(
            f"Output matrix shape ({result.shape}) does not match expected shape ({expected_result_shape})!"  # type: ignore
        )

    return result


def join_dataframes_recursively(dfs: List[pd.DataFrame]) -> pd.DataFrame:
    """Merges dataframes based on a divide and conquer strategy.

    Args:
        dfs (List[pd.DataFrame]): Unmerged dataframes read directly from files.

    Raises:
        ValueError: must contain at least one count file to merge.
        RuntimeError: sanity check to ensure the math is correct.
        RuntimeError: sanity check to ensure the dataframe shape matches what is expected.

    Returns:
        pd.DataFrame: a single, merged dataframe for all counts.
    """

    num_dfs = len(dfs)
    if num_dfs <= 0:
        raise ValueError("Must contain at least one count file to merge.")

    expected_result_shape = (dfs[0].shape[0], num_dfs)

    # Each iteration, the number of dataframes gets cut by 2.
    # During some iterations, there will be one dataframe left over without a mate to merge
    # with. Thus, we can calculate the number of mergings by following this pattern.
    num_iterations_needed = 0
    while num_dfs > 1:
        # ceil rounds up to account for the one dataframe without a mate case.
        amt = math.ceil(num_dfs / 2)
        num_iterations_needed += amt
        num_dfs = amt

    pbar = tqdm.tqdm(total=num_iterations_needed, desc="Merging recursively")
    while len(dfs) > 1:
        merged_dfs = []
        while len(dfs) > 0:
            one = dfs.pop(0)
            if len(dfs) == 0:
                # expected case where one df may be left alone on the stack
                merged_dfs.append(one)
            else:
                two = dfs.pop(0)
                merged = one.merge(two, how="outer", left_index=True, right_index=True)
                merged_dfs.append(merged)
            pbar.update()
        dfs = merged_dfs

    if not len(dfs) == 1:
        raise_error("Math was incorrect!")

    result = dfs[0]
    result.columns = sorted(result.columns)

    if not result.shape == expected_result_shape:
        raise_error(
            f"Output matrix shape ({result.shape}) does not match expected shape ({expected_result_shape})!"
        )

    return result


def concordance_test(dfs: List[pd.DataFrame]) -> None:
    """Performs a concordance test between the sequential and recursive strategies
    for merging matrices.

    Raises:
        AssertionError: if the matrices are not concordant.

    Args:
        dfs (List[pd.DataFrame]): Unmerged dataframes read directly from files.
    """

    logger.info("Concordance test has begun.")
    logger.info("Merging dataframes sequentially.")
    sequential_df = join_dataframes_sequentially(dfs)
    logger.info("Merging dataframes recursively.")
    recursive_df = join_dataframes_recursively(dfs)
    logger.info("Asserting concordance between the two matrices.")
    pd.testing.assert_frame_equal(sequential_df, recursive_df)
    logger.info("Testing completed, result were concordant.")


def run() -> None:
    """Main method for module.

    Raises:
        ValueError: If the user specifies a valid output file type that isn't handled in the code (unexpected).
    """

    args = get_args()
    logger.setLevel(logging.INFO)
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    logger.debug("Reading in counts data.")
    dfs = read_counts(
        args.counts,
        suffix_to_remove=args.file_suffix_to_remove,
        limit_inputs=args.limit_inputs,
    )

    if args.subcommand == "concordance-test":
        concordance_test(dfs)
    elif args.subcommand == "sequential" or args.subcommand == "recursive":
        logger.debug("Processing counts data using function %s .", args.func.__name__)
        result = args.func(dfs)

        output_file = "counts-matrix." + args.output_file_type
        if args.output_file:
            output_file = args.output_file

        logger.debug("Writing results to %s.", output_file)
        if args.output_file_type == "tsv":
            result.to_csv(output_file, sep="\t")
        elif args.output_file_type == "csv":
            result.to_csv(output_file)
        elif args.output_file_type == "hdf":
            result.to_hdf(output_file, "counts")
        else:
            raise ValueError(
                f"Unhandled output file type: {args.output_file_type}. Please contact the author."
            )

        logger.debug("Completed writing matrix.")
