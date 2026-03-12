from orchestral import define_tool
from taurex.cache import OpacityCache, CIACache

@define_tool()
def SetTaurexPaths(opacity_path: str, cia_path: str):
    """
    Set the paths for Taurex opacity and CIA files.

    Parameters:
    opacity_path (str): The path to the directory containing Taurex opacity files.
    cia_path (str): The path to the directory containing Taurex CIA files.

    This tool will set the paths in the OpacityCache and CIACache, which are used by Taurex to locate these files.
    These files are needed for the Taurex model to run successfully. They are usually in the linelists folder, if not ask the user to provide them.
    Note: the paths must use the full absolute path to the directories containing the files, not just the parent directory. Use `pwd` if you don't know the full path.
    """

    if opacity_path is not None:
        try:
            OpacityCache().set_opacity_path(opacity_path)
        except Exception as e:
            return(f"Error setting opacity path: {e}")
        print(f"Opacity path set to: {opacity_path}")
    else:
        print("No opacity path provided. Please provide a valid path.")

    if cia_path is not None:
        try:
            CIACache().set_cia_path(cia_path)
        except Exception as e:
            return(f"Error setting CIA path: {e}")
        print(f"CIA path set to: {cia_path}")
    else:
        print("No CIA path provided. Please provide a valid path.")

    return 'Paths set successfully!'


# def ensure_opacity_and_cia_paths(opa_path=None, cia_path=None, ask_user=True):
#     """
#     Ensure opacity AND CIA paths are set correctly in Taurex.
    
#     Checks if paths are set.
#     Verifies the folders exist and contain valid files.
#     Sets paths with OpacityCache and CIACache

#     opa_path : str or None Optional path to set for opacity files.
#     cia_path : str or None Optional path to set for CIA files.
#     ask_user : bool Whether to ask user for paths if missing.

#     Please redirect to this website https://taurex3.readthedocs.io/en/latest/ if an error occurs.
#     """

#     if opa_path is None:
#         print(f"Sacrebleu! No opacity path is set.")
#         #opa_path = input("Enter a valid opacity path: ").strip()
#     else:
#         OpacityCache().set_opacity_path(opa_path)

#     if cia_path is None:
#         print(f"Sacrebleu! No CIA path is set.")
#         #cia_path = input("Enter a valid CIA path: ").strip()
#     else:
#         CIACache().set_cia_path(cia_path)


#     print(f"Opacity path set successfully:\n{opa_path}!")
#     print(f"CIA path set successfully:\n{cia_path}!")

#     return opa_path, cia_path