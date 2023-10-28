import os
import numpy as np
from copy import copy
import commentjson as json
import colorama
from colorama import Fore, Back, Style
import xml
from copy import copy
import sys
from sys import exit
import os
import subprocess
import hashlib
import omegaconf

import numpy as np
import commentjson as json
from colorama import Fore, Back, Style
from cc.xmljson import XMLJSON

from . import csg
from .robot_description import RobotURDF#, RobotSDF
from .load_robot import OnShapeClient

class OnshapeRobotExporter:
    def __init__(self, config_path="./config.yaml"):
        self.config_path = config_path

        self.part_name_counts = {}
    
        if not os.path.exists(self.config_path):
            print("{STYLE}ERROR: The file {config_path} can't be found.{RESET}".format(
                STYLE=Fore.RED, config_path=self.config_path, RESET=Style.RESET_ALL))
            exit()

        self.processConfig()


        # convert workspace root path as absolute path
        self.root_directory = os.path.join(os.path.dirname(os.path.abspath(config_path)), self.config.outputDirectory.root)
        print("workspace path:", self.root_directory)


        self.robot = None

        # Creating robot for output
        if self.config.outputFormat == "urdf":
            self.robot = RobotURDF(self.config.robotName, self.config, self)
        # elif self.config.outputFormat == "sdf":
        #     self.robot = RobotSDF(self.config.robotName, self.config)
        else:
            print("{STYLE}RROR: Unknown output format: {output_format} supported are urdf and sdf {RESET}".format(
                STYLE=Fore.RED, output_format=self.config.outputFormat, RESET=Style.RESET_ALL))
            exit()
        
        self.createDirectories()
        
        
        self.client = OnShapeClient(self.config)
        

    def getConfig(self, name, default=None, has_default=False, values_list=None):
        has_default = has_default or (default is not None)
        if name in self.config.keys():
            value = self.config[name]
            if values_list is not None and value not in values_list:
                print("{STYLE}ERROR: Value for {name} should be one of: {values} {RESET}".format(
                    STYLE=Fore.RED, name=name, values=", ".join(values_list), RESET=Style.RESET_ALL))
                exit()
            return value
        else:
            if has_default:
                return default
            else:
                print("{STYLE}ERROR: missing key {name} in config {RESET}".format(
                    STYLE=Fore.RED, name=name, RESET=Style.RESET_ALL))
                exit()
    
    def processConfig(self):
        # validate configuration parameters
        self.config = omegaconf.OmegaConf.load(self.config_path)
        
        self.config["documentId"]       = self.getConfig("documentId")
        self.config["versionId"]        = self.getConfig("versionId", "")
        self.config["workspaceId"]      = self.getConfig("workspaceId", "")
        self.config["drawFrames"]       = self.getConfig("drawFrames", False)
        self.config["drawCollisions"]   = self.getConfig("drawCollisions", False)
        self.config["assemblyName"]     = self.getConfig("assemblyName", False)
        self.config["outputFormat"]     = self.getConfig("outputFormat", "urdf")
        self.config["useFixedLinks"]    = self.getConfig("useFixedLinks", False)
        self.config["configuration"]    = self.getConfig("configuration", "default")
        self.config["ignoreLimits"]     = self.getConfig("ignoreLimits", False)



        # Using OpenSCAD for simplified geometry
        self.config["useScads"]         = self.getConfig("useScads", True)
        self.config["pureShapeDilatation"] = self.getConfig("pureShapeDilatation", 0.0)

        # Dynamics
        self.config["jointMaxEffort"]   = self.getConfig("jointMaxEffort", 1)
        self.config["jointMaxVelocity"] = self.getConfig("jointMaxVelocity", 20)
        self.config["noDynamics"]       = self.getConfig("noDynamics", False)

        # Ignore list
        self.config["ignore"]           = self.getConfig("ignore", [])
        self.config["whitelist"]        = self.getConfig("whitelist", None, has_default=True)

        # Color override
        self.config["color"]            = self.getConfig("color", None, has_default=True)

        # STLs merge and simplification
        self.config["mergeSTLs"]        = self.getConfig("mergeSTLs", "no", values_list=[
                                            "no", "visual", "collision", "all"])
        self.config["maxSTLSize"]       = self.getConfig("maxSTLSize", 3)
        self.config["simplifySTLs"]     = self.getConfig("simplifySTLs", "no", values_list=[
                                            "no", "visual", "collision", "all"])

        # Post-import commands to execute
        self.config["postImportCommands"] = self.getConfig("postImportCommands", [])

        self.config["dynamicsOverride"] = {}

        # Add collisions=true configuration on parts
        self.config["useCollisionsConfigurations"] = self.getConfig(
            "useCollisionsConfigurations", True)

        # ROS support
        self.config["packageName"]      = self.getConfig("packageName", "")
        self.config["addDummyBaseLink"] = self.getConfig("addDummyBaseLink", False)
        self.config["robotName"]        = self.getConfig("robotName", "onshape")
        
        
        # additional XML code to insert
        if self.config['outputFormat'] == 'urdf':
            additionalFileName = self.getConfig('additionalUrdfFile', '')
        else:
            additionalFileName = self.getConfig('additionalSdfFile', '')

        # if additionalFileName == '':
        #     self.config['additionalXML'] = ''
        # else:
        #     with open(robot + additionalFileName, "r", encoding="utf-8") as stream:
        #         self.config['additionalXML'] = stream.read()


        # Creating dynamics override array
        tmp = self.getConfig('dynamics', {})
        for key in tmp:
            if tmp[key] == 'fixed':
                self.config['dynamicsOverride'][key.lower()] = {"com": [0, 0, 0], "mass": 0, "inertia": [
                    0, 0, 0, 0, 0, 0, 0, 0, 0]}
            else:
                self.config['dynamicsOverride'][key.lower()] = tmp[key]


        # Checking that OpenSCAD is present
        if self.config['useScads']:
            print("{STYLE} * Checking OpenSCAD presence...{RESET}".format(STYLE=Style.BRIGHT, RESET=Style.RESET_ALL))
            try:
                subprocess.run(["openscad", "-v"])
            except FileNotFoundError:
                print("{STYLE}Can't run openscad -v, disabling OpenSCAD support.{RESET}".format(STYLE=Fore.RED, RESET=Style.RESET_ALL))
                print(Fore.BLUE + "TIP: consider installing openscad:" + Style.RESET_ALL)
                print(Fore.BLUE + "Linux:" + Style.RESET_ALL)
                print(Fore.BLUE + "sudo add-apt-repository ppa:openscad/releases" + Style.RESET_ALL)
                print(Fore.BLUE + "sudo apt update" + Style.RESET_ALL)
                print(Fore.BLUE + "sudo apt install openscad" + Style.RESET_ALL)
                print(Fore.BLUE + "Windows:" + Style.RESET_ALL)
                print(Fore.BLUE + "go to: https://openscad.org/downloads.html " + Style.RESET_ALL)
                self.config["useScads"] = False

        # Checking that MeshLab is present
        if self.config['simplifySTLs']:
            print("{STYLE} * Checking MeshLab presence...{RESET}".format(STYLE=Style.BRIGHT, RESET=Style.RESET_ALL))
            if not os.path.exists('/usr/bin/meshlabserver') != 0:
                print(Fore.RED + "No /usr/bin/meshlabserver, disabling STL simplification support" + Style.RESET_ALL)
                print(Fore.BLUE + "TIP: consider installing meshlab:" + Style.RESET_ALL)
                print(Fore.BLUE + "sudo apt-get install meshlab" + Style.RESET_ALL)
                self.config['simplifySTLs'] = False

        # Checking that versionId and workspaceId are not set on same time
        if self.config["versionId"] != "" and self.config["workspaceId"] != "":
            print("{STYLE}You can't specify workspaceId AND versionId.{RESET}".format(STYLE=Style.RED, RESET=Style.RESET_ALL))

    def createDirectories(self):        
        # Output directory, making it if it doesn't exists
        if not os.path.exists(self.root_directory):
            os.makedirs(self.root_directory)

        part_dir_abs = os.path.join(self.root_directory, self.config.outputDirectory.parts)
        urdf_dir_abs = os.path.join(self.root_directory, self.config.outputDirectory.urdf)
        mesh_dir_abs = os.path.join(self.root_directory, self.config.outputDirectory.meshes)
        scad_dir_abs = os.path.join(self.root_directory, self.config.outputDirectory.scad)

        if not os.path.exists(part_dir_abs):
            os.makedirs(part_dir_abs)
        if not os.path.exists(urdf_dir_abs):
            os.makedirs(urdf_dir_abs)
        if not os.path.exists(mesh_dir_abs):
            os.makedirs(mesh_dir_abs)
        if not os.path.exists(scad_dir_abs):
            os.makedirs(scad_dir_abs)

    def buildRobot(self):
        root = self.client.tree
        self._buildRobot(root, np.matrix(np.identity(4)))

    def _buildRobot(self, tree, matrix):
        occurrence = self.client.getOccurrence([tree["id"]])
        
        instance = occurrence["instance"]
        
        print("{STYLE}* Adding top-level instance [{instance_name}] {RESET}".format(
            STYLE=Fore.BLUE + Style.BRIGHT, instance_name=instance["name"], RESET=Style.RESET_ALL))

        # Build a part name that is unique but still informative
        link_name = self.generateLinkName(instance["name"], instance["configuration"], occurrence["linkName"])
        print("link:", link_name)
        
        # if this is root
        if tree == self.client.tree:
            if self.config.addDummyBaseLink:
                self.robot.addDummyBaseLink(link_name)    

        # Create the link, collecting all children in the tree assigned to this top-level part
        self.resetLink()
        self.robot.createLink(link_name)
        
        for occurrence in self.client.occurrences.values():
            if occurrence["assignation"] == tree["id"] and occurrence["instance"]["type"] == "Part":
                self.addPart(link_name, occurrence, matrix)
        
        mass, com, inertia = self.linkDynamics()
        self.robot.endLink(link_name, mass, com, inertia)

        # Adding the frames (linkage is relative to parent)
        if tree["id"] in self.client.frames:
            for name, part in self.client.frames[tree["id"]]:
                frame = self.client.getOccurrence(part)["transform"]
                if self.robot.relative:
                    frame = np.linalg.inv(matrix)*frame
                self.robot.addFrame(name, frame)

        # Following the children in the tree, calling this function recursively
        for child in tree["children"]:
            world_axis_frame = child["axis_frame"]
            z_axis = child["z_axis"]
            joint_type = child["jointType"]
            joint_limits = child["jointLimits"]

            if self.robot.relative:
                axis_frame = np.linalg.inv(matrix) * world_axis_frame
                child_matrix = world_axis_frame
            else:
                # In SDF format, everything is expressed in the world frame, in this case
                # childMatrix will be always identity
                axis_frame = world_axis_frame
                child_matrix = matrix

            child_link_name = self._buildRobot(child, child_matrix)
            
            print("joint:", link_name, " <---> ", child_link_name)
            self.robot.addJoint(joint_type, link_name, child_link_name, axis_frame,
                        child["dof_name"], joint_limits, z_axis)

        return link_name
    
    def resetLink(self):
        self._mesh = {'visual': None, 'collision': None}
        self._color = np.array([0., 0., 0.])
        self._color_mass = 0
        self._link_childs = 0
        self._visuals = []
        self._dynamics = []
        
    def linkDynamics(self):
        mass = 0
        com = np.array([0.0]*3)
        inertia = np.matrix(np.zeros((3, 3)))
        identity = np.matrix(np.eye(3))

        for dynamic in self._dynamics:
            mass += dynamic['mass']
            com += dynamic['com']*dynamic['mass']

        if mass > 0:
            com /= mass

        # https://pybullet.org/Bullet/phpBB3/viewtopic.php?t=246
        for dynamic in self._dynamics:
            r = dynamic['com'] - com
            p = np.matrix(r)
            inertia += dynamic['inertia'] + \
                (np.dot(r, r)*identity - p.T*p)*dynamic['mass']

        return mass, com, inertia

    
    def partIsIgnore(self, name):
        if self.config['whitelist'] is None:
            return name in self.config['ignore']
        else:
            return name not in self.config['whitelist']

    # Adds a part to the current robot link
    def addPart(self, link_name, occurrence, matrix):
        part = occurrence["instance"]

        # Checking if this part should be ignored
        if part["suppressed"]:
            return

        if part["partId"] == "":
            print("{STYLE}WARNING: Part '{name}' has no partId{RESET}".format(
                STYLE=Fore.YELLOW, name=part["name"], RESET=Style.RESET_ALL))
            return

        print("Add Part:", link_name)
        
        
        # Importing STL file for this part
        base_part_name, full_part_name = self.extractPartName(part["name"], part["configuration"])

        configuration_info = ""
        if occurrence["instance"]["configuration"] != "default":
            configuration_info = "{STYLE} (configuration: '{config}')".format(
                STYLE=Style.DIM, config=occurrence["instance"]["configuration"])
            
        symbol = "+"
        if self.partIsIgnore(base_part_name):
            symbol = "-"
            configuration_info += "{STYLE} / ignoring visual and collision".format(Style.DIM)

        print("{STYLE}{symbol} Adding part {name} {configuration_info} {RESET}".format(
            STYLE=Fore.GREEN,
            symbol=symbol,
            name=occurrence["instance"]["name"],
            configuration_info=configuration_info,
            RESET=Style.RESET_ALL))

        if self.partIsIgnore(base_part_name):
            stl_path = None
        else:
            stl_filename = "{link_name}_{part_name}.stl".format(link_name=link_name, part_name=full_part_name)
            
            stl_path = os.path.join(self.config.outputDirectory.meshes, stl_filename)
            stl_metadata_path = os.path.join(self.config.outputDirectory.parts, full_part_name+".part")
            
            # shorten the configuration to a maximum number of chars to prevent errors. Necessary for standard parts like screws
            if len(part["configuration"]) > 40:
                shortend_configuration = hashlib.md5(
                    part["configuration"].encode("utf-8")).hexdigest()
            else:
                shortend_configuration = part["configuration"]
            
            stl = self.client.fetchSTL(part, shortend_configuration)
            
            stl_path_abs = os.path.join(self.root_directory, stl_path)
            with open(stl_path_abs, "wb") as stream:
                stream.write(stl)

            stl_metadata_path_abs = os.path.join(self.root_directory, stl_metadata_path)
            with open(stl_metadata_path_abs, "w", encoding="utf-8") as stream:
                json.dump(part, stream, indent=2, sort_keys=True)




        # Import the SCAD files pure shapes
        shapes = None
        
        self.config['useScads'] = True
        if self.config['useScads']:
            scadFile = full_part_name+'.scad'
            scad_path = os.path.join(self.root_directory, self.config.outputDirectory.scad, scadFile)
            if os.path.exists(scad_path):
                shapes = csg.process(
                    scad_path, self.config['pureShapeDilatation'])
            else:
                print("generating SCAD!")
                with open(scad_path, 'w', encoding="utf-8") as stream:
                    stream.write("")



        # Obtain metadatas about part to retrieve color
        if self.config['color'] is not None:
            color = self.config['color']
        else:
            metadata = self.client.client.part_get_metadata(
                part['documentId'], part['documentMicroversion'], part['elementId'], part['partId'], part['configuration'])

            color = [0.5, 0.5, 0.5]

            # XXX: There must be a better way to retrieve the part color
            for entry in metadata['properties']:
                if 'value' in entry and type(entry['value']) is dict and 'color' in entry['value']:
                    rgb = entry['value']['color']
                    color = np.array(
                        [rgb['red'], rgb['green'], rgb['blue']])/255.0



        # Obtain mass properties about that part
        if self.config["noDynamics"]:
            mass = 0
            com = [0]*3
            inertia = [0]*12
        else:
            if full_part_name in self.config['dynamicsOverride']:
                entry = self.config['dynamicsOverride'][full_part_name]
                mass = entry['mass']
                com = entry['com']
                inertia = entry['inertia']
            else:
                mass_properties = self.client.client.part_mass_properties(
                    part['documentId'], part['documentMicroversion'], part['elementId'], part['partId'], part['configuration'])

                if part['partId'] not in mass_properties['bodies']:
                    print(Fore.YELLOW + 'WARNING: part ' +
                        part['name']+' has no dynamics (maybe it is a surface)' + Style.RESET_ALL)
                    return
                mass_properties = mass_properties['bodies'][part['partId']]
                mass = mass_properties['mass'][0]
                com = mass_properties['centroid']
                inertia = mass_properties['inertia']

                if abs(mass) < 1e-9:
                    print(Fore.YELLOW + 'WARNING: part ' +
                        part['name']+' has no mass, maybe you should assign a material to it ?' + Style.RESET_ALL)

        pose = occurrence['transform']
        if self.robot.relative:
            pose = np.linalg.inv(matrix)*pose

        self.robot.addPart(link_name, pose, stl_path, mass, color, shapes, full_part_name)
        
        self.addLinkDynamics(pose, mass, com, inertia)
        
        
    def addLinkDynamics(self, matrix, mass, com, inertia):
        # Inertia
        I = np.matrix(np.reshape(inertia[:9], (3, 3)))
        R = matrix[:3, :3]
        # Expressing COM in the link frame
        com = np.array(
            (matrix*np.matrix([com[0], com[1], com[2], 1]).T).T)[0][:3]
        # Expressing inertia in the link frame
        inertia = R*I*R.T

        self._dynamics.append({
            'mass': mass,
            'com': com,
            'inertia': inertia
        })

    def extractPartName(self, name, configuration):
        name_lst = name.split(" ")
        base_name = "_".join(name_lst[:-1]).lower()
        config_name = ""
        
        # only add configuration to name if its not default and not a very long configuration (which happens for library parts like screws)
        if configuration != "default" and len(configuration) < 40:
            config_name = "_" + configuration.replace("=", "_").replace(" ", "_")
            
        full_part_name = base_name + config_name
        
        return base_name, full_part_name

    """
    Convert Onshape part name to a legal link name
    
    This method will read in onshape part names, replace all spaces with underscores, and remove the "<" ">" symbol for part instance numbers.
    """
    def generateLinkName(self, name, configuration, override_name=None):
        if override_name:
            return override_name
        
        _, name = self.extractPartName(name, configuration)

        if name in self.part_name_counts:
            self.part_name_counts[name] += 1
        else:
            self.part_name_counts[name] = 1

        if self.part_name_counts[name] == 1:
            return name
        else:
            return name+'_'+str(self.part_name_counts[name])
    
    def reorderJoints(self):
        reorder_joint = [None] * len(self.config.jointOrder)
        reorder_joint_extra = []
        
        for j in self.robot.json["robot"]["joint"]:
            try:
                desired_index = self.config.jointOrder.index(j["name"])
                print(desired_index)
                reorder_joint[desired_index] = j
            except omegaconf.errors.ConfigValueError:
                reorder_joint_extra.append(j)
        
        for j in reorder_joint:
            if j is None:
                reorder_joint.remove(j)
            
        reorder_joint.extend(reorder_joint_extra)
        
        print([j["name"] for j in reorder_joint])
        self.robot.json["robot"]["joint"] = reorder_joint

    def write(self):
        json.dump(self.robot.json, open(os.path.join(self.root_directory, self.config.outputDirectory.urdf, "robot.json"), "w"), indent=2)


        xml_tree = XMLJSON.gdata.etree(self.robot.json)[0]
        xml.etree.ElementTree.indent(xml_tree, space="  ", level=0)
        xml_data = xml.etree.ElementTree.tostring(xml_tree, encoding="utf8")
        
        print("\n" + Style.BRIGHT + "* Writing " +
            self.robot.ext.upper()+" file" + Style.RESET_ALL)
        with open(os.path.join(self.root_directory, self.config.outputDirectory.urdf, "robot."+self.robot.ext), "wb") as stream:
            stream.write(xml_data)


        if len(self.config['postImportCommands']):
            print("\n" + Style.BRIGHT + "* Executing post-import commands" + Style.RESET_ALL)
            for command in self.config['postImportCommands']:
                print("* "+command)
                os.system(command)


def main():
    colorama.just_fix_windows_console()

    config_path = sys.argv[1]

    exporter = OnshapeRobotExporter(config_path) 
    
    # Start building the robot
    exporter.buildRobot()

    exporter.robot.finalize()
    # print(exporter.robot.json)
    
    exporter.reorderJoints()
    exporter.write()
    
    # mjcf configs
    mjcf_data = {
        "mujoco": {
            "model": "humanoid",
            "statistic": {
                "extent": "2",
                "center": "0 0 1",
            },
            "option": {
                "timestep": "0.00555",
            },
            "default": {
                "motor": {
                    "ctrlrange": "-1 1",
                    "ctrllimited": True,
                },
                "default": {
                    "class": "body",
                    "geom": {
                        "type": "capsule",
                        "condim": 1,
                        "friction": "1.0 0.05 0.05",
                        "solimp": ".9 .99 .003",
                        "solref": ".015 1",
                    },
                    "joint": {
                        "type": "hinge",
                        "damping": 0.1,
                        "stiffness": 5,
                        "armature": .007,
                        "limited": True,
                        "solimplimit": "0 .99 .01",
                    },
                    "site": {
                        "size": 0.04,
                        "group": 3,
                    },
                    "default": [
                        {
                            "class": "force-torque",
                            "site": {
                                "type": "box",
                                "size": ".01 .01 .02",
                                "rgba": "1 0 0 1",
                            },
                        },
                        {
                            "class": "touch",
                            "site": {
                                "type": "capsule",
                                "rgba": "0 0 1 .3",
                            },
                        }
                    ]
                }
            },
            "worldbody": {
                "geom": {
                    "name": "floor",
                    "type": "plane",
                    "conaffinity": 1,
                    "size": "100 100 .2",
                    "material": "grid",
                },
                "body": {
                    "name": "pelvis",
                    "pos": "0 0 1",
                    "childclass": "body",
                    "freejoint": {
                        "name": "root",
                    },
                },
            },
        },
    }
    xml_tree = XMLJSON.gdata.etree(mjcf_data)[0]
    xml.etree.ElementTree.indent(xml_tree, space="  ", level=0)
    xml_data = xml.etree.ElementTree.tostring(xml_tree, encoding="utf8")
    
    ext = "mjcf"
    
    xml_data = xml_data.replace(b"<?xml version='1.0' encoding='utf8'?>", b"")    
    print("\n" + Style.BRIGHT + "* Writing " +
        ext.upper()+" file" + Style.RESET_ALL)
    with open(os.path.join(exporter.root_directory, exporter.config.outputDirectory.urdf, "robot."+ext), "wb") as stream:
        stream.write(xml_data)
        
    # End of MJCF
        


if __name__ == "__main__":
    if len(sys.argv) <= 1:
        print(Fore.RED +
            'ERROR: usage: onshape-to-robot {robot_directory}' + Style.RESET_ALL)
        print("Read documentation at https://onshape-to-robot.readthedocs.io/")
        exit("")
    
    main()
