
import os
import sys
import sysconfig
from setuptools import setup
from setuptools import find_packages
from setuptools.command.test import test as test
from distutils.extension import Extension
import distutils.cmd
import distutils.log
import pickle
import pprint
import hashlib
import subprocess
from subprocess import call
import shutil
import re


class Tox(test):

    """Setuptools test command to run Tox."""

    user_options = [('tox-args=', 'a', "Arguments to pass to tox")]

    def initialize_options(self):
        """Initialize test."""
        test.initialize_options(self)
        self.tox_args = None

    def finalize_options(self):
        """Initialize test."""
        test.finalize_options(self)
        self.test_args = []
        self.test_suite = True

    def run_tests(self):
        """Do the actual running of the tests."""
        # import here, cause outside the eggs aren't loaded
        import tox
        import shlex
        args = self.tox_args
        if args:
            args = shlex.split(self.tox_args)
        errno = tox.cmdline(args=args)
        sys.exit(errno)


class CythonModule(object):

    """Cython module to be compiled in Unreal Engine ecosystem."""

    def __init__(self, pyx, announce):
        """Construct a CythonModule from the pyx path provided."""
        super(CythonModule, self).__init__()
        pyx = os.path.abspath(pyx)
        if not os.path.exists(pyx):
            raise Exception('Bad pyx path: {}'.format(pyx))
        self.pyx = pyx
        pxd, __ = os.path.splitext(pyx)
        pxd = '{}.pxd'.format(pxd)
        if os.path.exists(pxd):
            self.pxd = pxd
        self.name, __ = os.path.splitext(os.path.basename(pyx))
        # turn foo_bar into UnrealPy_FooBar
        self.unreal_name = 'UnrealPy_{}'.format(
            self.name.title().replace('_', ''))
        self.pch = 'Private/{}PrivatePCH.h'.format(self.unreal_name)
        # this isn't right, but #yolo
        self.announce = announce

    def __repr__(self):
        """String repsresentation of module."""
        return "{}".format(self.name)

    def generate_cython(self, unreal_path):
        """Generate cython for this module."""
        cython_output_file = self.cython_output_path(unreal_path)
        output_dir = os.path.dirname(cython_output_file)
        # make sure output dir exists
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        if call([
                'cython',
                '--verbose',
                '--cplus',
                '-o', cython_output_file,
                self.pyx]) == 0:
            pass
        else:
            raise Exception("cython command exited with non-zero status"
                            " for {0}".format(self.name))
        # post-process the output
        # add PCH include to make UE happy
        lines = []
        inc_pattern = re.compile('^\s*#include\s+"([\w\.]+)"')
        py_inc, py_lib, py_lib_name = python_vars()
        with open(cython_output_file) as fin:
            for line in fin:
                m = inc_pattern.match(line)
                if m and m.group(1):
                    replacement = 'Python/{}'.format(m.group(1))
                    if os.path.exists(os.path.join(py_inc, replacement)):
                        # we prefix python header include with Python/
                        line = line.replace(
                            m.group(1), replacement)
                        self.announce(
                            "Replacing include to '{}' with '{}'".format(
                                m.group(1), replacement),
                            distutils.log.INFO)
                lines.extend([line])
        with open(cython_output_file, 'w') as fout:
            fout.write("\n#include \"{}\"\n\n".format(self.pch))
            for line in lines:
                fout.write(line)
        return cython_output_file

    def generate_unreal_module(self, unreal_path):
        """Setup UE4 module for Cython module."""
        build_file_path = os.path.join(
            self.unreal_module_path(unreal_path),
            '{}.Build.cs'.format(self.unreal_name))
        py_inc, py_lib, py_lib_name = python_vars()
        build_file_contents = """
// This file is generated by setup.py and edits will be overwritten!

using UnrealBuildTool;
using System.Diagnostics;
using System.IO;

public class {module_name} : ModuleRules
{{
    public {module_name}(TargetInfo Target)
    {{
        // Cython does some shadowing :|
        bEnableShadowVariableWarnings = false;

        PublicDependencyModuleNames.AddRange(
            new string[] {{
                "Core",
                "CoreUObject",
                "Engine",
                "UnrealEd",
            }}
        );

        // Python
        PrivateIncludePaths.Add("{python_include_path}");
        PublicLibraryPaths.Add("{python_lib_path}");
        PublicAdditionalLibraries.Add("{python_lib}");
    }}
}}
""".format(module_name=self.unreal_name,
            python_include_path=py_inc.replace('\\', '\\\\'),
            python_lib_path=py_lib.replace('\\', '\\\\'),
            python_lib=os.path.join(py_lib, py_lib_name).replace('\\', '\\\\'))
        with open(build_file_path, 'w') as f:
            f.write(build_file_contents)
        self.add_to_ue_editor_target(unreal_path)
        self.create_pch(unreal_path)

    def add_to_ue_editor_target(self, unreal_path):
        """Inject UE module name into UE4Editor.Target.cs."""
        editor_target_path = os.path.join(
            unreal_path, 'Engine', 'Source', 'UE4Editor.Target.cs')
        target_lines = []
        marker_found = False
        with open(editor_target_path, 'r') as fread:
            for line in fread:
                if marker_found:
                    target_lines.extend([line])
                    continue
                else:
                    target_lines.extend([line])
                    if "@UNREALPY@" in line:
                        marker_found = True
                        target_lines.extend([
                            "OutExtraModuleNames.Add(\"{}\");\n".format(
                                self.unreal_name)])
        with open(editor_target_path, 'w') as fwrite:
            fwrite.write("".join(target_lines))

    def create_pch(self, unreal_path):
        """Create PCH for module if it doesn't exist."""
        pch_contents = """
// This file is generated by setup.py and edits will be overwritten!

#pragma warning (disable:4510)
#pragma warning (disable:4610)
#pragma warning (disable:4146)

#include "Core.h"
"""
        with open(os.path.join(
                unreal_path, 'Engine', 'Source', 'Editor',
                self.unreal_name, self.pch), 'w') as f:
            f.write(pch_contents)

    def checksum(self):
        """Get checksum of module."""
        # todo : recursively check dependencies too (from cimport+include)
        #        this is very temporary...
        hasher = hashlib.sha256()
        hasher.update(file_hash(self.pyx))
        if hasattr(self, 'pxd'):
            hasher.update(file_hash(self.pxd))
        return hasher.hexdigest()

    def python_lib_path(self, in_source_dir=False):
        """Get path as a Python module."""
        library_ext = None
        if sys.platform == 'win32':
            library_ext = '.pyd'
        elif sys.platform == 'darwin':
            library_ext = '.so'
        else:
            raise Exception('Unsupported platform: {}'.format(sys.platform))
        filename = '{}{}'.format(self.name, library_ext)
        if in_source_dir:
            return os.path.join(
                os.path.abspath(os.path.dirname(self.pyx)),
                filename)
        rel_path = os.path.relpath(os.path.dirname(self.pyx), '.')
        base_path = os.path.join('build', 'lib')
        return os.path.join(base_path, rel_path, filename)

    def unreal_lib_path(self, unreal_config, unreal_path):
        """Get path as a Unreal Engine module binary."""
        unreal_library_name = None
        library_ext = None
        unreal_platform = None
        binary_path_prefix = ''
        if sys.platform == 'win32':
            library_ext = '.dll'
            unreal_platform = 'Win64'
        elif sys.platform == 'darwin':
            library_ext = '.dylib'
            unreal_platform = 'Mac'
            if unreal_config == 'Development':
                binary_path_prefix = 'UE4Editor.app/Contents/MacOS'
            elif unreal_config == 'Debug':
                binary_path_prefix = 'UE4Editor-Mac-Debug.app/Contents/MacOS'
        else:
            raise Exception("Unsupported platform: {}".format(sys.platform))
        if unreal_config == 'Development':
            unreal_library_name = 'UE4Editor-{}{}'.format(
                self.unreal_name,
                library_ext)
        elif unreal_config == 'Debug':
            unreal_library_name = 'UE4Editor-{}-{}-Debug{}'.format(
                self.unreal_name,
                unreal_platform,
                library_ext)
        else:
            raise Exception('Unknown unreal_config: {}'.format(unreal_config))
        return os.path.join(
            unreal_path,
            'Engine',
            'Binaries',
            unreal_platform,
            binary_path_prefix,
            unreal_library_name)

    def cython_output_path(self, unreal_path):
        """Get path to generated Cython output."""
        return os.path.join(
            self.unreal_module_path(unreal_path),
            'Private',
            self.unreal_name + '.cpp')

    def unreal_module_path(self, unreal_path):
        """Get path of Unreal module directory."""
        return os.path.join(
            unreal_path,
            'Engine', 'Source', 'Editor',
            self.unreal_name)


class GenerateReadmeCommand(distutils.cmd.Command):

    """
    A custom command to generate README.txt in reStructuredTxt from README.md.
    """

    description = 'generate README.txt from README.md'
    user_options = [
        ('pandoc-path=', None, 'Path to Pandoc')
    ]

    def initialize_options(self):
        """Set default values for options."""
        self.pandoc_path = os.environ.get('PANDOC_PATH')

    def finalize_options(self):
        """Post-process options."""
        assert self.pandoc_path and os.path.exists(self.pandoc_path), (
            'pandoc-path not set or doesn\'t exist.')

    def run(self):
        import pandoc
        doc = pandoc.Document()
        doc.markdown = open('README.md').read()
        f = open('README.txt', 'w+')
        f.write(doc.rst)
        f.close()


class BuildUnrealCommand(distutils.cmd.Command):

    """
    A custom command to generate and compile Unreal Engine API.

    Will detect pyx files, generate UE modules for them,
    compile these modules in the UE ecosystem,
    and extract the compiled libraries.
    """

    description = 'generate and compile Unreal API'
    user_options = [
        ('unreal-path=', None, 'Path to UE4 source base'),
        ('unreal-config=', None, 'Unreal build config [Debug, Development]'),
        ('stage-in-source', None, 'Stage build artefacts into source'),
        ('rebuild', None, 'Force rebuild'),
    ]
    module_checksum_path = os.path.join(
        os.path.abspath(os.path.dirname(__file__)),
        '.cache',
        'cython_module_checksums.p')

    def initialize_options(self):
        """Set default values for options."""
        self.unreal_path = os.environ.get('UNREAL_PATH')
        self.unreal_config = os.environ.get('UNREAL_CONFIG') or 'Development'
        self.stage_in_source = False
        self.rebuild = False
        self.include_dirs = None
        self.library_dirs = None

    def finalize_options(self):
        """Post-process options."""
        assert self.unreal_path, (
            'unreal-path not set.')
        self.unreal_path = os.path.expanduser(self.unreal_path)
        assert os.path.exists(self.unreal_path), (
            'unreal-path %s not found.' % self.unreal_path)
        assert os.path.exists(os.path.join(self.unreal_path, 'Engine')), (
            'unreal-path %s not sane, no Engine dir found.' % self.unreal_path)

    def find_modules(self):
        """Search recursively for .pyx files."""
        modules = []
        for root, dirs, files in os.walk(
                os.path.abspath(os.path.dirname(__file__))):
            for file in files:
                file_path = os.path.join(root, file)
                if not file.endswith('.pyx'):
                    continue
                self.announce(
                    "Found .pyx file @ {0}".format(file_path),
                    level=distutils.log.INFO)
                module = CythonModule(file_path, self.announce)
                modules.extend([module])
        return modules

    def filter_changed_modules(self, modules):
        """Remove modules that haven't changed since previous build."""
        checksums = self.load_checksums()
        if len(checksums) == 0:
            self.announce(
                "No checksums found, everything is dirty",
                distutils.log.INFO)
            return modules
        self.announce("loaded checksums: {}".format(pprint.pformat(checksums)), distutils.log.INFO)
        for module in modules:
            self.announce(
                'module: {}\n\tchecksum: {}\n\told checksum known: {}\n\tchecksum changed: {}\n\tbinary exists: {}'.format(
                    module.name,
                    module.checksum(),
                    module.name in checksums,
                    module.checksum() != checksums[module.name],
                    os.path.exists(module.unreal_lib_path(
                        self.unreal_config, self.unreal_path))),
                distutils.log.INFO)
        return [module for module in modules
                if module.name not in checksums
                or module.checksum() != checksums[module.name]
                or not os.path.exists(module.unreal_lib_path(
                    self.unreal_config, self.unreal_path))]

    def generate_cython(self, modules):
        """Generate Cythonized C++ to later compile."""
        for module in modules:
            output_path = module.generate_cython(self.unreal_path)
            self.announce(
                'Generated {}'.format(output_path),
                distutils.log.INFO)

    def clean_unreal_editor_target(self):
        """Remove injected lines in UE4Editor.Target.cs."""
        editor_target_path = os.path.join(
            self.unreal_path, 'Engine', 'Source', 'UE4Editor.Target.cs')
        target_lines = []
        marker_found = False
        end_marker_found = False
        with open(editor_target_path, 'r') as fread:
            for line in fread:
                if end_marker_found or not marker_found:
                    target_lines.extend([line])
                    if "@UNREALPY@" in line:
                        marker_found = True
                elif marker_found and "@/UNREALPY@" in line:
                    end_marker_found = True
                    target_lines.extend([line])
        with open(editor_target_path, 'w') as fwrite:
            fwrite.write("".join(target_lines))
        if not marker_found or not end_marker_found:
            raise Exception(
                "Unable to work with UE4Editor.Target.cs. "
                "You need to manually add the following lines somewhere "
                "in SetupBinaries(...) among the OutExtraModuleNames.Add(...) "
                "calls:\n"
                "// @UNREALPY@\n"
                "// @/UNREALPY@")

    def generate_unreal_module(self, modules):
        """Generate Unreal Engine Build.cs files for provided modules."""
        for module in modules:
            module.generate_unreal_module(self.unreal_path)

    def load_checksums(self):
        """Load the checksum dict from file."""
        if os.path.exists(self.module_checksum_path):
            return pickle.load(open(self.module_checksum_path, 'rb'))
        return {}

    def save_checksums(self, modules):
        """Save the checksum dict to file."""
        checksums = self.load_checksums()
        for module in modules:
            checksums[module.name] = module.checksum()
        checksum_dir = os.path.dirname(self.module_checksum_path)
        if not os.path.exists(checksum_dir):
        	os.makedirs(checksum_dir)
        pickle.dump(checksums, open(self.module_checksum_path, 'wb'))

    def generate_unreal_project(self):
        """Run Unreal Engine script to generate project files."""
        script_ext = None
        if sys.platform == 'win32':
            script_ext = '.bat'
        elif sys.platform == 'darwin':
            script_ext = '.sh'
        else:
            raise Exception('Unsupported platform: {}'.format(sys.platform))
        gen_script = os.path.join(
            self.unreal_path,
            'GenerateProjectFiles' + script_ext)
        for output in run(gen_script, self.unreal_path):
            self.announce(
                'Unreal-GenerateProjectFiles > {}'.format(
                    output.replace('\n', '')),
                distutils.log.INFO)

    def invoke_unreal_build(self):
        """Invoke the Unreal Engine build system."""
        unreal_build_script = None
        unreal_platform = None
        if sys.platform == 'win32':
            unreal_build_script = os.path.join(
                self.unreal_path, 'Engine', 'Build',
                'BatchFiles', 'Build.bat')
            unreal_platform = 'Win64'
        elif sys.platform == 'darwin':
            unreal_build_script = os.path.join(
                self.unreal_path, 'Engine', 'Build',
                'BatchFiles', 'Mac', 'Build.sh')
            unreal_platform = 'Mac'
        self.announce(
            'Running {}'.format(unreal_build_script), distutils.log.INFO)
        for output in run([
                unreal_build_script,
                'UE4Editor',
                unreal_platform,
                self.unreal_config],
                self.unreal_path):
            self.announce(
                'Unreal-Build > {}'.format(output.replace('\n', '')),
                distutils.log.INFO)

    def extract_libraries(self):
        """
        Find and retrieve build libraries from UE build location.

        Will copy libraries from the location where Unreal builds them,
        Engine/Binaries/<platform>/... and stage it into this package
        for Python import, changing the name and suffix accordingly.
        """
        modules = self.find_modules()
        for module in modules:
            from_path = module.unreal_lib_path(
                self.unreal_config,
                self.unreal_path)
            to_path = module.python_lib_path(self.stage_in_source)
            base_dir = os.path.dirname(os.path.abspath(__file__))
            self.announce(
                '{}\n\t-> {}'.format(
                    from_path.replace(self.unreal_path, ''),
                    to_path),
                distutils.log.INFO)
            to_dir = os.path.dirname(to_path)
            if not os.path.exists(to_dir):
                os.makedirs(to_dir)
            shutil.copyfile(from_path, to_path)

    def run(self):
        """Actually run the command."""
        modules = self.find_modules()
        self.announce(
            "Found modules:\n{}".format(pprint.pformat(modules)),
            distutils.log.INFO)
        if not self.rebuild:
            modules = self.filter_changed_modules(modules)
            self.announce(
                "Modules considered dirty:\n{}".format(
                    pprint.pformat(modules)),
                distutils.log.INFO)
        if len(modules) > 0:
            self.generate_cython(modules)
            self.clean_unreal_editor_target()
            self.generate_unreal_module(modules)
            self.generate_unreal_project()
            self.invoke_unreal_build()
            self.save_checksums(modules)
        self.extract_libraries()


def read(fname):
    """Return the contents of fname relative to setup.py, used for README."""
    return open(os.path.join(os.path.dirname(__file__), fname)).read()


def file_hash(fname, blocksize=65536):
    """Return the checksum of file at fname."""
    hasher = hashlib.sha256()
    with open(fname, 'rb') as f:
        buf = f.read(blocksize)
        while len(buf) > 0:
            hasher.update(buf)
            buf = f.read(blocksize)
    return hasher.digest()


def run(exe, cwd):
    """Run provided exe and yield output."""
    if type(exe) is str:
        exe = [exe]
    p = subprocess.Popen(
        exe,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT)
    while True:
        retcode = p.poll()
        line = p.stdout.readline()
        yield line
        if retcode is not None:
            if retcode != 0:
                # todo: this is 0 even if UBT throws an error :(
                raise Exception(
                    '{} failed with status {}'.format(' '.join(exe), retcode))
            break


def python_vars():
    """Get include path, lib path, and lib name for Python dev env."""
    python_base = os.environ.get('PYTHON_BASE')
    if not python_base:
        raise Exception('PYTHON_BASE environment variable need'
                        'to point to Python root directory.')
    lib_name = None
    if sys.platform == 'win32':
        lib_name = 'python27.lib'
    elif sys.platform == 'darwin':
        lib_name = 'libpython2.7.dylib'
    if not lib_name:
        raise Exception("Unsupported platform: {}".format(sys.platform))
    header_to_find = os.path.join(python_base, 'include_unrealpy', 'Python', 'Python.h')
    if not os.path.exists(header_to_find):
        raise Exception("""
----------------------------------------
Python environment requires manual setup,
Didn't find expected header {header}
----

1. Inside PYTHON_BASE ({python_base})
   create a directory called 'include_unrealpy'

2. inside this directory create a symlink (directory junction on Windows)
   called 'Python' which points back to PYTHON_BASE/include
   ({desired_inc}),
   that's ../include in relative terms.

This is needed because Python's headers will collide with Unreal's,
e.g. datetime.h with DateTime.h. Putting everything in a subdirectory
we'll include it as "Python/Python.h" and most things will be happy.
There will however be issues when Cython wants to use Unreal's DateTime.h
and instead accidentally include Python's. Sorry that this is so hacky,
I'm eager for a better solution...

----
""".format(python_base=python_base,
            desired_inc=os.path.join(python_base, 'include'),
            header=header_to_find))
    inc_path = os.path.join(python_base, 'include_unrealpy')
    lib_path = os.path.join(python_base, 'lib')
    return inc_path, lib_path, lib_name


def find_pyx(dir, files=[]):
    """Recursively search dir for .pyx files."""
    for file in os.listdir(dir):
        path = os.path.join(dir, file)
        if os.path.isfile(path) and path.endswith(".pyx"):
            files.append(path.replace(os.path.sep, ".")[:-4])
        elif os.path.isdir(path):
            find_pyx(path, files)
    return files


def make_extension(name):
    """Create and Extension object from a given name."""
    ext_path = name.replace(".", os.path.sep) + ".pyx"
    return Extension(
        name,
        [ext_path],
        include_dirs=[],
        libraries=[],
    )


def distutils_dir_name(dname):
    """Return the name of a distutils build directory."""
    f = "{dirname}.{platform}-{version[0]}.{version[1]}"
    return f.format(dirname=dname,
                    platform=sysconfig.get_platform(),
                    version=sys.version_info)

# ext_names = find_pyx("unrealpy")
# extensions = [make_extension(name) for name in ext_names]


long_description = None
if os.path.exists('README.txt'):
    long_description = open('README.txt').read()

setup(
    name="unrealpy",
    version="0.0.1",
    author="Tobias Mollstam",
    author_email="tobias@mollstam.com",
    description=("A Python API for the Unreal Engine 4 Editor"),
    license="MIT",
    keywords="unreal ue4 gamedev",
    long_description=long_description,
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Topic :: Utilities",
        "License :: OSI Approved :: MIT License",
    ],
    tests_require=['tox'],
    cmdclass={
        'test': Tox,
        'build_ue': BuildUnrealCommand,
        'build_ext': BuildUnrealCommand,
        'generate_readme': GenerateReadmeCommand,
    },
    #packages=['unrealpy'],
    packages=find_packages(),
    package_data={'': ['*.so', '*.pyd']},
    # ext_modules=extensions,
)
